<script setup lang="ts">
/**
 * 报告中心（PRD FR-57~FR-63）。
 *
 * 布局（PRD 7.3）：顶部筛选（类型 / 主题 / 状态）+ 发起生成 → 左栏报告列表
 * → 右栏在线预览与导出，生成中显示进度与阶段（FR-62 的交互要点）。
 *
 * 五条实现约定：
 *
 * 1. **生成是异步长任务**（实测 53~60 秒）：POST 立即 202 返回 `generating` 的报告，
 *    页面**选中它并轮询**到终态。轮询而不是只依赖 SSE：SSE 的进度只有 0/50/100 三档，
 *    而「成没成、失败原因是什么」必须以库里的状态为准（SSE 断线也不该让页面停在「生成中」）。
 *    SSE 负责「看得见在动」（进度条 + 阶段文案），轮询负责「知道结束了」。
 * 2. **列表筛选走服务端**：类型（周期性简报/专项）是页面上的一级分类，
 *    只筛当前页会让第二页的同类报告凭空消失——看着对、实际不全。
 * 3. **PDF 走浏览器打印**（D-07）：导出 html（服务端渲染的自包含页面，样式面向 A4 优化），
 *    在新标签页打开并唤起打印，另存为 PDF。**不用 `window.print()` 打印本页预览**：
 *    本页预览是 Markdown 子集的排版，与服务端交付的 HTML 不是同一份东西，
 *    打印出来的和下载到的不一致比「多一步」更让人困惑。
 * 4. **失败给原因与重试**（FR-62）：失败原因取自 `error_message`（材料不全 / 模型不可用），
 *    并可以用同一个目标重试。
 * 5. **正文只从详情接口取**：列表接口不返回正文（一页报告可能上百 KB）；
 *    预览按需拉取，避免列表页背着几十份正文。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { eventApi, reportApi, topicApi } from '@/api/modules'
import { ApiError } from '@/api/client'
import ReportPreview from '@/components/report/ReportPreview.vue'
import type {
  EventSummary,
  ReportCreatePayload,
  ReportDetail,
  ReportItem,
  ReportStatus,
  ReportType,
  TopicSummary,
} from '@/api/types'
import { REPORT_STATUS_LABELS, REPORT_TYPE_LABELS } from '@/api/types'
import { saveBlob, openBlob } from '@/utils/download'
import { formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import { useRealtimeStore } from '@/stores/realtime'

const auth = useAuthStore()
const realtime = useRealtimeStore()

const PAGE_SIZE = 10
/** 生成期的轮询间隔。3 秒与报告总时长（约 1 分钟）相比足够灵敏，又不至于打爆库 */
const POLL_INTERVAL_MS = 3_000
/** 报告引擎在事件总线里的角色名（见 contracts/roles.py 的 ROLE_REPORT） */
const REPORT_ROLE = 'report'
/** 标题由 query 确定性派生（service.report_title），重试时据此反推 query */
const TITLE_PREFIX = /^舆情(?:研判报告|周期简报)：/
/** 状态下拉的取值：与后端枚举同值，标签同源（写错字面值不报错但筛选恒为空） */
const STATUS_OPTIONS = Object.keys(REPORT_STATUS_LABELS) as ReportStatus[]

const canGenerate = computed(() => auth.hasRole('analyst', 'admin'))

// ---------------------------------------------------------------------------
// 列表与筛选
// ---------------------------------------------------------------------------

const reports = ref<ReportItem[]>([])
const total = ref(0)
const page = ref(1)
const loadingList = ref(false)
const topics = ref<TopicSummary[]>([])

const filters = reactive({
  report_type: '' as ReportType | '',
  status: '' as ReportStatus | '',
  topic_id: '',
})

async function loadReports(): Promise<void> {
  loadingList.value = true
  try {
    const result = await reportApi.list({
      page: page.value,
      page_size: PAGE_SIZE,
      report_type: filters.report_type || undefined,
      status: filters.status || undefined,
      topic_id: filters.topic_id || undefined,
    })
    reports.value = result.items
    total.value = result.total
    // 当前选中的报告被筛掉了就换到第一条，避免右侧停在一份「列表里没有」的报告上
    if (!reports.value.some((item) => item.report_id === selectedId.value)) {
      selectReport(reports.value[0]?.report_id ?? '')
    }
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    loadingList.value = false
  }
}

async function loadTopics(): Promise<void> {
  try {
    topics.value = (await topicApi.list({ status: 'enabled', page_size: 100 })).items
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  }
}

function resetFilters(): void {
  filters.report_type = ''
  filters.status = ''
  filters.topic_id = ''
  page.value = 1
  void loadReports()
}

watch(() => [filters.report_type, filters.status, filters.topic_id], () => {
  page.value = 1
  void loadReports()
})

// ---------------------------------------------------------------------------
// 详情与预览
// ---------------------------------------------------------------------------

const selectedId = ref('')
const detail = ref<ReportDetail | null>(null)
const loadingDetail = ref(false)
/** 导出中的按钮标识（md / html / pdf），用于按按钮显示 loading */
const exporting = ref('')

function previewMeta(item: ReportDetail): { label: string; value: string }[] {
  return [
    { label: '类型', value: item.type_label },
    { label: '状态', value: item.status_label },
    { label: '字数', value: item.chars ? `${item.chars} 字` : '-' },
    { label: '生成时间', value: formatDateTime(item.generated_at ?? item.created_at) },
  ]
}

async function selectReport(reportId: string): Promise<void> {
  if (!reportId) {
    selectedId.value = ''
    detail.value = null
    stopPolling()
    return
  }
  selectedId.value = reportId
  await loadDetail(reportId)
}

async function loadDetail(reportId: string): Promise<void> {
  loadingDetail.value = true
  try {
    detail.value = await reportApi.detail(reportId)
    if (detail.value.status === 'generating') {
      startPolling(reportId)
    } else {
      stopPolling()
    }
  } catch (error) {
    detail.value = null
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    loadingDetail.value = false
  }
}

// ---------------------------------------------------------------------------
// 生成中的轮询与进度
// ---------------------------------------------------------------------------

let pollTimer: ReturnType<typeof setInterval> | null = null
/** 轮询周期里顺带推进的时钟：用来显示「已耗时 N 秒」，避免再开一个定时器 */
const nowTick = ref(Date.now())

function startPolling(reportId: string): void {
  stopPolling()
  pollTimer = setInterval(() => {
    nowTick.value = Date.now()
    void pollOnce(reportId)
  }, POLL_INTERVAL_MS)
}

function stopPolling(): void {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function pollOnce(reportId: string): Promise<void> {
  try {
    const latest = await reportApi.detail(reportId)
    detail.value = latest
    if (latest.status === 'generating') return

    // 到终态：停轮询、刷新列表（状态标签与字数都变了）、给一句结论
    stopPolling()
    await loadReports()
    if (latest.status === 'generated') {
      ElMessage.success(`报告已生成（${latest.chars} 字）`)
    } else {
      ElMessage.error(`报告生成失败：${latest.error_message ?? '原因未知'}`)
    }
  } catch (error) {
    // 轮询失败不打断生成：下一轮再试；只在鉴权失效时让拦截器处理
    if (error instanceof ApiError && !error.isAuthError && error.code !== 'NETWORK') {
      stopPolling()
      ElMessage.error(error.message)
    }
  }
}

/** 生成进度百分比：报告角色只发 0/50/100，如实显示后端给的值 */
const progressPercent = computed(() => {
  const value = realtime.progress[REPORT_ROLE]
  return typeof value === 'number' ? Math.min(100, Math.max(0, value)) : 0
})

const progressMessage = computed(
  () => realtime.progressMessage[REPORT_ROLE] || '正在生成报告（约 1 分钟）',
)

/** 已耗时（秒）。以创建时间为起点：进程重启后恢复的报告也能算出真实耗时 */
const elapsedSeconds = computed(() => {
  const started = detail.value?.created_at
  if (!started) return 0
  return Math.max(0, Math.floor(nowTick.value / 1000) - started)
})

// ---------------------------------------------------------------------------
// 发起生成
// ---------------------------------------------------------------------------

const dialogVisible = ref(false)
const submitting = ref(false)
const events = ref<EventSummary[]>([])
const form = reactive({
  target: 'event' as ReportType,
  event_id: '',
  topic_id: '',
})

function openDialog(): void {
  form.event_id = events.value[0]?.event_id ?? ''
  form.topic_id = topics.value[0]?.topic_id ?? ''
  dialogVisible.value = true
}

function buildPayload(): ReportCreatePayload | null {
  if (form.target === 'event') {
    return form.event_id ? { event_id: form.event_id } : null
  }
  return form.topic_id ? { topic_id: form.topic_id } : null
}

async function submitGenerate(): Promise<void> {
  const payload = buildPayload()
  if (!payload) {
    ElMessage.warning(form.target === 'event' ? '请选择事件' : '请选择主题')
    return
  }
  submitting.value = true
  try {
    const created = await reportApi.generate(payload)
    dialogVisible.value = false
    ElMessage.success('已发起生成，可在列表中查看进度（约 1 分钟）')
    page.value = 1
    await loadReports()
    await selectReport(created.report_id)
  } catch (error) {
    // 同一目标已有生成中的报告会被拒（409），提示里带了那份报告的 ID
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    submitting.value = false
  }
}

/** 重试：沿用原目标。只给了 query 的报告按标题前缀反推 query（标题由 query 派生） */
function retryPayload(item: ReportItem): ReportCreatePayload | null {
  if (item.event_id) return { event_id: item.event_id }
  if (item.topic_id) return { topic_id: item.topic_id }
  return TITLE_PREFIX.test(item.title) ? { query: item.title.replace(TITLE_PREFIX, '') } : null
}

async function retry(item: ReportItem): Promise<void> {
  const payload = retryPayload(item)
  if (!payload) {
    ElMessage.warning('无法确定这份报告的目标，请用「生成报告」重新发起')
    return
  }
  try {
    const created = await reportApi.generate(payload)
    ElMessage.success('已重新发起生成')
    await loadReports()
    await selectReport(created.report_id)
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  }
}

// ---------------------------------------------------------------------------
// 导出（FR-61 / D-07）
// ---------------------------------------------------------------------------

async function exportFile(fileType: 'md' | 'html'): Promise<void> {
  const item = detail.value
  if (!item) return
  exporting.value = fileType
  try {
    const { blob, filename, hint } = await reportApi.download(item.report_id, fileType)
    saveBlob(blob, filename)
    if (fileType === 'html' && hint === 'pdf-by-browser-print') {
      // 后端用这个头告诉前端 PDF 的正确路径（D-07），原样转告用户
      ElMessage.info('已下载自包含 HTML：用浏览器打开后打印即可另存为 PDF')
    } else {
      ElMessage.success(`已导出 ${fileType.toUpperCase()}：${filename}`)
    }
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    exporting.value = ''
  }
}

/** 导出 PDF：拿服务端渲染的 HTML，在新标签页打开并唤起打印 */
async function exportPdf(): Promise<void> {
  const item = detail.value
  if (!item) return
  exporting.value = 'pdf'
  try {
    const { blob, filename } = await reportApi.download(item.report_id, 'html')
    const { window: opened, revoke } = openBlob(blob)
    if (!opened) {
      // 弹窗被拦截时退回下载，而不是什么都不发生
      saveBlob(blob, filename)
      ElMessage.warning('浏览器拦截了新标签页，已改为下载 HTML：打开后 Ctrl+P 可另存为 PDF')
      return
    }
    try {
      opened.addEventListener('load', () => {
        opened.focus()
        opened.print()
      })
    } catch {
      // 少数浏览器不允许跨文档挂事件：退化为让用户自己按打印
    }
    ElMessage.info('已在新标签页打开报告：打印对话框里选择「另存为 PDF」')
    setTimeout(revoke, 60_000)
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    exporting.value = ''
  }
}

function statusTagType(status: ReportStatus): 'success' | 'warning' | 'danger' {
  if (status === 'generated') return 'success'
  return status === 'failed' ? 'danger' : 'warning'
}

/**
 * 列表行的导出入口：先切到这份报告再导出。
 *
 * 导出的是「当前预览的报告」的产物，因此必须先把选中态切过去——
 * 否则用户在 A 报告的行上点「导出」，拿到的是 B 报告的产物。
 */
async function exportFromList(item: ReportItem, command: string): Promise<void> {
  if (item.report_id !== selectedId.value) {
    await selectReport(item.report_id)
  }
  if (command === 'pdf') {
    await exportPdf()
  } else {
    await exportFile(command as 'md' | 'html')
  }
}

// ---------------------------------------------------------------------------
// 生命周期
// ---------------------------------------------------------------------------

onMounted(async () => {
  await Promise.all([loadReports(), loadTopics()])
  try {
    events.value = (await eventApi.list({ page_size: 50 })).items
  } catch {
    // 事件列表只用于生成对话框的下拉：拿不到不该影响报告中心的其余功能
  }
})

onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="page">
    <!-- 筛选与生成入口（FR-57/58） -->
    <el-card shadow="never" class="filter-card">
      <div class="filters">
        <el-radio-group v-model="filters.report_type" size="default">
          <el-radio-button value="">全部类型</el-radio-button>
          <el-radio-button value="periodic">周期性简报</el-radio-button>
          <el-radio-button value="event">突发事件专项</el-radio-button>
        </el-radio-group>

        <el-select v-model="filters.topic_id" placeholder="全部主题" clearable class="w-180">
          <el-option v-for="topic in topics" :key="topic.topic_id"
                     :label="topic.name" :value="topic.topic_id" />
        </el-select>

        <el-select v-model="filters.status" placeholder="全部状态" clearable class="w-140">
          <el-option v-for="status in STATUS_OPTIONS" :key="status"
                     :label="REPORT_STATUS_LABELS[status]" :value="status" />
        </el-select>

        <el-button :disabled="!filters.report_type && !filters.status && !filters.topic_id"
                   @click="resetFilters">重置</el-button>
        <span class="count">共 <b>{{ total }}</b> 份</span>

        <div class="spacer" />
        <el-tooltip
          :disabled="canGenerate"
          content="发起生成需要「报告生成」权限（分析师与管理员）"
        >
          <span>
            <el-button type="primary" :disabled="!canGenerate" @click="openDialog">
              生成报告
            </el-button>
          </span>
        </el-tooltip>
      </div>
    </el-card>

    <el-row :gutter="16">
      <!-- 左：报告列表（FR-57） -->
      <el-col :span="8">
        <el-card shadow="never" class="column-card" v-loading="loadingList">
          <template #header>
            <span>报告列表</span>
            <span class="card-hint">点击查看在线预览</span>
          </template>

          <div class="report-list">
            <div
              v-for="item in reports"
              :key="item.report_id"
              class="report-item"
              :class="{ active: item.report_id === selectedId }"
              @click="selectReport(item.report_id)"
            >
              <div class="item-title">《{{ item.title }}》</div>
              <div class="item-meta">
                <el-tag size="small" effect="plain">{{ REPORT_TYPE_LABELS[item.report_type] }}</el-tag>
                <el-tag size="small" :type="statusTagType(item.status)" effect="dark">
                  {{ item.status_label }}
                </el-tag>
                <span>{{ formatDateTime(item.generated_at ?? item.created_at) }}</span>
                <span v-if="item.chars">{{ item.chars }} 字</span>
              </div>
              <div v-if="item.status === 'failed'" class="item-error">
                {{ item.error_message ?? '生成失败' }}
              </div>
              <div class="item-actions" @click.stop>
                <el-button size="small" text type="primary" @click="selectReport(item.report_id)">
                  预览
                </el-button>
                <el-dropdown
                  :disabled="item.status !== 'generated'"
                  @command="(command: string) => exportFromList(item, command)"
                >
                  <el-button size="small" text :disabled="item.status !== 'generated'">
                    导出<el-icon><ArrowDown /></el-icon>
                  </el-button>
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item command="md">下载 Markdown</el-dropdown-item>
                      <el-dropdown-item command="html">下载 HTML</el-dropdown-item>
                      <el-dropdown-item command="pdf">打印为 PDF</el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
                <el-button
                  v-if="canGenerate && item.status === 'failed'"
                  size="small"
                  text
                  type="warning"
                  @click="retry(item)"
                >
                  重试
                </el-button>
              </div>
            </div>

            <el-empty v-if="!reports.length && !loadingList"
                      description="暂无报告（点右上角「生成报告」发起一份）" :image-size="80" />
          </div>

          <el-pagination
            v-if="total > PAGE_SIZE"
            v-model:current-page="page"
            :page-size="PAGE_SIZE"
            :total="total"
            layout="prev, pager, next"
            small
            class="pager"
            @current-change="loadReports"
          />
        </el-card>
      </el-col>

      <!-- 右：在线预览与导出（FR-60/61） -->
      <el-col :span="16">
        <el-card shadow="never" class="column-card">
          <template #header>
            <div class="preview-head">
              <span>在线预览</span>
              <template v-if="detail">
                <div class="preview-actions">
                  <el-button
                    size="small"
                    :loading="exporting === 'md'"
                    :disabled="detail.status !== 'generated'"
                    @click="exportFile('md')"
                  >
                    导出 Markdown
                  </el-button>
                  <el-button
                    size="small"
                    :loading="exporting === 'html'"
                    :disabled="detail.status !== 'generated'"
                    @click="exportFile('html')"
                  >
                    导出 HTML
                  </el-button>
                  <el-button
                    size="small"
                    type="primary"
                    :loading="exporting === 'pdf'"
                    :disabled="detail.status !== 'generated'"
                    @click="exportPdf"
                  >
                    导出 PDF
                  </el-button>
                </div>
              </template>
            </div>
          </template>

          <div v-loading="loadingDetail" class="preview-body">
            <template v-if="detail">
              <!-- 生成中：进度与阶段（FR-62 的交互要点） -->
              <div v-if="detail.status === 'generating'" class="generating">
                <el-progress
                  :percentage="progressPercent"
                  :stroke-width="10"
                  striped
                  striped-flow
                />
                <p class="generating-text">
                  {{ progressMessage }}
                  <span class="elapsed">已耗时 {{ elapsedSeconds }} 秒</span>
                </p>
                <p class="hint">
                  报告由多智能体产物汇总而成，约 1 分钟完成。可以离开本页，
                  完成后列表里的状态会变为「已生成」。
                </p>
              </div>

              <!-- 失败：原因 + 重试（FR-62） -->
              <div v-else-if="detail.status === 'failed'" class="failed">
                <el-alert type="error" :closable="false" show-icon title="报告生成失败">
                  <template #default>
                    <p class="failed-reason">{{ detail.error_message ?? '未记录失败原因' }}</p>
                    <p class="hint">
                      材料不全会如实失败而不是产出一份半成品——先补齐事件内容与研判产物再重试。
                    </p>
                  </template>
                </el-alert>
                <div class="failed-actions">
                  <el-button v-if="canGenerate" type="primary" @click="retry(detail)">
                    用同一目标重试
                  </el-button>
                  <span v-else class="hint">当前角色不可发起生成，请联系分析师或管理员</span>
                </div>
              </div>

              <!-- 已生成：正文 -->
              <ReportPreview
                v-else
                :markdown="detail.content_md"
                :title="detail.title"
                :meta="previewMeta(detail)"
              />
            </template>

            <el-empty v-else description="选择左侧报告以预览" :image-size="80" />
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 发起生成（FR-58） -->
    <el-dialog v-model="dialogVisible" title="发起报告生成" width="520px">
      <el-form label-width="88px">
        <el-form-item label="报告类型">
          <el-radio-group v-model="form.target">
            <el-radio-button value="event">突发事件专项</el-radio-button>
            <el-radio-button value="periodic">周期性简报</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-form-item v-if="form.target === 'event'" label="目标事件">
          <el-select v-model="form.event_id" placeholder="选择事件" class="w-full">
            <el-option v-for="event in events" :key="event.event_id"
                       :label="`${event.title}（${event.stats.content_count} 条）`"
                       :value="event.event_id" />
          </el-select>
        </el-form-item>

        <el-form-item v-else label="目标主题">
          <el-select v-model="form.topic_id" placeholder="选择主题" class="w-full">
            <el-option v-for="topic in topics" :key="topic.topic_id"
                       :label="topic.name" :value="topic.topic_id" />
          </el-select>
        </el-form-item>
      </el-form>

      <p class="dialog-hint">
        生成是异步任务（约 1 分钟）：报告材料来自该主题/事件已有的研判产物，
        材料不全会如实失败并说明缺什么。同一目标已有生成中的报告时会被拒绝，避免重复跑。
      </p>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitGenerate">
          开始生成
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.filter-card {
  margin-bottom: 16px;
}

.filters {
  display: flex;
  align-items: center;
  gap: 12px;
}

.filters .spacer {
  flex: 1;
}

.count {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.column-card {
  min-height: 620px;
}

.card-hint {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
  color: var(--color-text-secondary);
}

.report-list {
  min-height: 520px;
}

.report-item {
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.report-item + .report-item {
  margin-top: 10px;
}

.report-item:hover {
  border-color: var(--el-color-primary-light-5);
}

.report-item.active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.item-title {
  font-size: 14px;
  font-weight: 600;
  line-height: 1.6;
}

.item-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 10px;
  margin-top: 6px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.item-error {
  margin-top: 6px;
  font-size: 12px;
  color: var(--risk-a);
}

.item-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 6px;
}

.pager {
  margin-top: 12px;
  justify-content: center;
}

.preview-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.preview-actions {
  display: flex;
  gap: 8px;
}

.preview-body {
  min-height: 560px;
  max-height: calc(100vh - 240px);
  overflow-y: auto;
  padding-right: 6px;
}

.generating {
  padding: 60px 24px 0;
}

.generating-text {
  margin: 14px 0 0;
  font-size: 13px;
  text-align: center;
}

.elapsed {
  margin-left: 10px;
  color: var(--color-text-secondary);
}

.failed {
  padding: 24px 12px;
}

.failed-reason {
  margin: 6px 0;
  font-size: 13px;
  line-height: 1.7;
  word-break: break-word;
}

.failed-actions {
  margin-top: 16px;
}

.hint {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--color-text-secondary);
}

.dialog-hint {
  margin: 0;
  font-size: 12px;
  line-height: 1.8;
  color: var(--color-text-secondary);
}

.w-140 {
  width: 140px;
}

.w-180 {
  width: 180px;
}

.w-full {
  width: 100%;
}
</style>
