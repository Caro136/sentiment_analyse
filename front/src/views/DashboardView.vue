<script setup lang="ts">
/**
 * 舆情态势大盘（PRD FR-36~FR-41）。
 *
 * 六个区块与需求的对应：指标卡 FR-40 / 声量趋势 FR-36 / 情感分布 FR-37 /
 * 告警滚动 FR-38 / 热词云 FR-39 / 自动刷新 FR-41。
 *
 * 三条实现约定：
 *
 * 1. **一次请求取全六个区块的数据**（`/dashboard/metrics` 一次给五个指标 + `/alerts` 取告警）。
 *    拆分请求会得到「趋势是新的、分布是旧的」——同一屏的数字必须来自同一时刻。
 * 2. **所有图表点击都下钻到研判工作台并携带筛选条件**（设计规格 3.4）。
 *    参数名与 `WorkbenchView` 的约定一致（见 front/README「下钻参数」）。
 *    告警行的「处置」是另一类下钻：指定**处置对象**（event_id + alert_id）而非筛选条件。
 * 3. **自动刷新在页面不可见时暂停**：大盘常年开着，隐藏标签页里继续每分钟请求
 *    纯属浪费（`document.visibilityState` 判断，切回来立刻补一次）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { alertApi, dashboardApi, topicApi } from '@/api/modules'
import type {
  AlertItem,
  AlertStatus,
  DashboardMetrics,
  HotWord,
  RiskLevel,
  SourceType,
  TopicSummary,
  UserRole,
} from '@/api/types'
import { RISK_LABELS } from '@/api/types'
import { ApiError } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useChart } from '@/utils/chart'
import { formatCount, formatPercent, formatTime } from '@/utils/format'

const router = useRouter()
const auth = useAuthStore()

/** 时间范围选项：hours 直接传给后端（窗口口径见 README「大盘指标口径」） */
const RANGE_OPTIONS = [
  { label: '近 24 小时', value: 24 },
  { label: '近 7 天', value: 168 },
  { label: '近 30 天', value: 720 },
]

/** 自动刷新周期：与后端物化周期（60 秒）一致，快于它只会读同一份缓存 */
const REFRESH_INTERVAL_MS = 60_000

const loading = ref(false)
const metrics = ref<DashboardMetrics | null>(null)
const alerts = ref<AlertItem[]>([])
const topics = ref<TopicSummary[]>([])
const selectedTopic = ref<string>('')
const hours = ref<number>(168)
const lastLoadedAt = ref<number | null>(null)

const trendRef = ref<HTMLElement>()
const sentimentRef = ref<HTMLElement>()
const trendChart = useChart(trendRef)
// 情感环形图点击扇区即下钻（FR-37 的下钻口径）
const sentimentChart = useChart(sentimentRef, {
  onClick: (params) => drillToSentiment(params.name),
})

const overview = computed(() => metrics.value?.metrics.overview)
const negativeRatioText = computed(() => {
  // null 表示窗口内没有内容——不能显示成 0%，那会被读成「统计过了，全都不负面」
  return formatPercent(overview.value?.negative_ratio)
})

const canAckAlerts = computed(() => auth.hasRole('analyst', 'pr'))

/** 词云：字号按权重线性映射，颜色区分来源（词包词=品牌色，实体=中性色） */
const wordCloud = computed(() => {
  const items = metrics.value?.metrics.hot_words.items ?? []
  if (!items.length) return []
  const max = Math.max(...items.map((w) => w.weight))
  const min = Math.min(...items.map((w) => w.weight))
  const span = max - min || 1
  return items.map((word) => ({
    ...word,
    // 字号 13~30px：太小看不清，太大挤成一团
    size: Math.round(13 + ((word.weight - min) / span) * 17),
    color: word.kind === 'keyword' ? 'var(--el-color-primary)' : '#5a6472',
  }))
})

// ---------------------------------------------------------------------------
// 数据加载
// ---------------------------------------------------------------------------

async function loadMetrics(): Promise<void> {
  const data = await dashboardApi.metrics({
    topic_id: selectedTopic.value || undefined,
    hours: hours.value,
  })
  metrics.value = data
  renderCharts()
}

async function loadAlerts(): Promise<void> {
  const page = await alertApi.list({ page_size: 8 })
  alerts.value = page.items
}

async function refresh(): Promise<void> {
  if (loading.value) return
  loading.value = true
  try {
    // 并行取数：两块数据互不依赖
    await Promise.all([loadMetrics(), loadAlerts()])
    lastLoadedAt.value = Date.now()
  } catch (error) {
    // 401 已由拦截器统一跳登录，这里只提示其余错误
    if (error instanceof ApiError && !error.isAuthError) {
      ElMessage.error(error.message)
    }
  } finally {
    loading.value = false
  }
}

async function loadTopics(): Promise<void> {
  try {
    const page = await topicApi.list({ status: 'enabled', page_size: 100 })
    topics.value = page.items
  } catch {
    // 主题下拉取不到不影响大盘主体（只是少一个筛选维度），不打扰用户
  }
}

// ---------------------------------------------------------------------------
// 图表
// ---------------------------------------------------------------------------

function renderCharts(): void {
  const trend = metrics.value?.metrics.volume_trend
  if (trend?.points.length) {
    trendChart.render({
      tooltip: { trigger: 'axis' },
      legend: { data: ['声量', '负向'], right: 0, top: 0 },
      grid: { left: 8, right: 16, bottom: 8, top: 32, containLabel: true },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        // 桶的时间戳转成可读标签；后端已按粒度分好桶
        data: trend.points.map((p) => formatBucket(p.bucket_start, trend.bucket_seconds)),
      },
      yAxis: { type: 'value', minInterval: 1 },
      series: [
        {
          name: '声量',
          type: 'line',
          smooth: true,
          showSymbol: false,
          areaStyle: { opacity: 0.12 },
          data: trend.points.map((p) => p.total),
        },
        {
          name: '负向',
          type: 'line',
          smooth: true,
          showSymbol: false,
          itemStyle: { color: 'var(--sentiment-negative)' },
          lineStyle: { color: 'var(--sentiment-negative)' },
          data: trend.points.map((p) => p.negative),
        },
      ],
    })
  }

  const sentiment = metrics.value?.metrics.sentiment_distribution
  const counts = sentiment?.counts ?? {}
  if (sentiment?.total) {
    sentimentChart.render({
      tooltip: { trigger: 'item', formatter: '{b}：{c} 条（{d}%）' },
      legend: { bottom: 0 },
      series: [
        {
          type: 'pie',
          radius: ['52%', '74%'],
          avoidLabelOverlap: true,
          // 只标百分比：名称交给底部图例。实测带名称的标签在 1/3 宽度的卡片里
          // 会被容器裁成「负向 42…」，而图例本来就写了名称，重复且占地方
          label: { formatter: '{d}%' },
          labelLine: { length: 8, length2: 8 },
          data: [
            { name: '正向', value: counts.positive ?? 0, itemStyle: { color: 'var(--sentiment-positive)' } },
            { name: '中性', value: counts.neutral ?? 0, itemStyle: { color: 'var(--sentiment-neutral)' } },
            { name: '负向', value: counts.negative ?? 0, itemStyle: { color: 'var(--sentiment-negative)' } },
          ],
        },
      ],
    })
  }
}

function formatBucket(timestamp: number, bucketSeconds: number): string {
  const date = new Date(timestamp * 1000)
  const month = `${date.getMonth() + 1}`.padStart(2, '0')
  const day = `${date.getDate()}`.padStart(2, '0')
  const hour = `${date.getHours()}`.padStart(2, '0')
  return bucketSeconds >= 86_400 ? `${month}-${day}` : `${month}-${day} ${hour}时`
}

// ---------------------------------------------------------------------------
// 下钻（设计规格 3.4：所有图表点击都带筛选条件跳工作台）
// ---------------------------------------------------------------------------

interface DrillQuery {
  topic_id?: string
  source_type?: SourceType
  sentiment?: string
  risk_level?: RiskLevel
  keyword?: string
  start_time?: number
  end_time?: number
}

function drillDown(query: DrillQuery): void {
  const topicId = selectedTopic.value || undefined
  router.push({ name: 'workbench', query: { topic_id: topicId, ...query, from: 'dashboard' } })
}

function drillToSentiment(name: string): void {
  const map: Record<string, string> = { 正向: 'positive', 中性: 'neutral', 负向: 'negative' }
  const sentiment = map[name]
  if (sentiment) drillDown({ sentiment })
}

function drillToSource(item: { source_type: SourceType }): void {
  drillDown({ source_type: item.source_type })
}

function drillToWord(word: HotWord): void {
  drillDown({ keyword: word.word })
}

function drillToAlerts(level?: RiskLevel): void {
  // 告警没有独立页面：跳工作台并把风险等级带上，去看对应的内容与事件
  drillDown({ risk_level: level })
}

/**
 * 告警 → 处置（FR-38 → FR-49 的链路）：直接带着事件与告警 ID 跳工作台，
 * 由工作台把事件区切到该事件、开单时把告警 ID 一并提交。
 *
 * 与上面的 `drillDown` 不同，这条不是「按条件筛内容」而是「指定处置对象」，
 * 因此参数是 event_id / alert_id 而不是筛选条件。
 */
function drillToDispose(alert: AlertItem): void {
  if (!alert.event_id) return
  router.push({
    name: 'workbench',
    query: { event_id: alert.event_id, alert_id: alert.alert_id, from: 'alert' },
  })
}

// ---------------------------------------------------------------------------
// 告警认领（FR-38：S/A 行高亮并支持一键认领）
// ---------------------------------------------------------------------------

const acking = ref<string>('')

async function claimAlert(alert: AlertItem): Promise<void> {
  acking.value = alert.alert_id
  try {
    const updated = await alertApi.updateStatus(alert.alert_id, {
      status: 'claimed',
      note: '大盘一键认领',
    })
    // 就地更新而不是整表重拉：重拉会让列表在用户眼前跳动
    const index = alerts.value.findIndex((item) => item.alert_id === updated.alert_id)
    if (index >= 0) alerts.value[index] = updated
    ElMessage.success('已认领')
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    acking.value = ''
  }
}

function canClaim(alert: AlertItem): boolean {
  // 已关闭的告警不能再认领；后端也会拦（终态不可流转）
  return alert.status !== 'closed' && alert.status !== 'claimed'
}

function alertRowClass(alert: AlertItem): string {
  // S/A 级高亮（FR-38 的验收要求）
  return alert.level === 'S' || alert.level === 'A' ? `alert-row alert-row-${alert.level}` : 'alert-row'
}

const STATUS_TAG_TYPES: Record<AlertStatus, 'danger' | 'warning' | 'info' | 'success'> = {
  unread: 'danger',
  read: 'warning',
  claimed: 'info',
  closed: 'success',
}

function statusTagType(status: AlertStatus): 'danger' | 'warning' | 'info' | 'success' {
  return STATUS_TAG_TYPES[status]
}

// ---------------------------------------------------------------------------
// 自动刷新（FR-41）
// ---------------------------------------------------------------------------

let timer: ReturnType<typeof setInterval> | null = null

function startAutoRefresh(): void {
  if (timer) return
  timer = setInterval(() => {
    // 页面不可见时跳过这一轮；用户切回来时 watch 会立刻补一次
    if (document.visibilityState === 'visible') void refresh()
  }, REFRESH_INTERVAL_MS)
}

function stopAutoRefresh(): void {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

function handleVisibility(): void {
  if (document.visibilityState === 'visible') void refresh()
}

onMounted(async () => {
  await Promise.all([loadTopics(), refresh()])
  startAutoRefresh()
  document.addEventListener('visibilitychange', handleVisibility)
})

onBeforeUnmount(() => {
  stopAutoRefresh()
  document.removeEventListener('visibilitychange', handleVisibility)
})

// 筛选条件变化即重新取数（主题/时间范围）
watch([selectedTopic, hours], () => {
  void refresh()
})

const roleLabel = computed(() => {
  const labels: Record<UserRole, string> = {
    analyst: '舆情分析师',
    pr: '公关运营',
    admin: '系统管理员',
  }
  return auth.role ? labels[auth.role] : ''
})
</script>

<template>
  <div class="page" v-loading="loading">
    <!-- 筛选栏 -->
    <div class="toolbar">
      <el-select v-model="selectedTopic" placeholder="全部主题" clearable size="default" class="topic-select">
        <el-option v-for="topic in topics" :key="topic.topic_id" :label="topic.name" :value="topic.topic_id" />
      </el-select>
      <el-radio-group v-model="hours" size="default">
        <el-radio-button v-for="range in RANGE_OPTIONS" :key="range.value" :value="range.value">
          {{ range.label }}
        </el-radio-button>
      </el-radio-group>
      <span class="refresh-hint">
        <template v-if="metrics?.computed_at">
          数据更新于 {{ formatTime(metrics.computed_at) }}
          <el-tag size="small" type="info" effect="plain">每 60 秒自动刷新</el-tag>
        </template>
      </span>
      <el-button :icon="'Refresh'" :loading="loading" @click="refresh">刷新</el-button>
      <span class="role-hint">{{ roleLabel }}</span>
    </div>

    <!-- 核心指标卡（FR-40） -->
    <el-row :gutter="16" class="metrics">
      <el-col :span="6">
        <el-card shadow="never" class="metric-card">
          <div class="metric-label">总声量</div>
          <div class="metric-value">{{ formatCount(overview?.total_volume) }}</div>
          <div class="metric-sub">条（含评论）</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never" class="metric-card">
          <div class="metric-label">负面率</div>
          <div class="metric-value negative">{{ negativeRatioText }}</div>
          <div class="metric-sub">负向 {{ overview?.negative_count ?? 0 }} 条</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never" class="metric-card clickable" @click="drillToAlerts('S')">
          <div class="metric-label">S/A 级事件</div>
          <div class="metric-value danger">{{ overview?.high_risk_event_count ?? '—' }}</div>
          <div class="metric-sub">其中 S/A 级内容 {{ overview?.high_risk_content_count ?? 0 }} 条</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never" class="metric-card clickable" @click="router.push({ name: 'reports' })">
          <div class="metric-label">待处置工单</div>
          <div class="metric-value">{{ overview?.open_ticket_count ?? '—' }}</div>
          <div class="metric-sub">未关闭告警 {{ overview?.open_alert_count ?? 0 }} 条</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 趋势 + 情感 -->
    <el-row :gutter="16">
      <el-col :span="16">
        <el-card shadow="never">
          <template #header>
            <span>全网声量趋势</span>
            <span class="card-hint">点击曲线下钻到工作台</span>
          </template>
          <div ref="trendRef" class="chart chart-trend"></div>
          <el-empty v-if="!metrics?.metrics.volume_trend.points.length" description="窗口内暂无数据" :image-size="70" />
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never">
          <template #header>
            <span>情感倾向分布</span>
            <span class="card-hint">点击扇区下钻</span>
          </template>
          <div ref="sentimentRef" class="chart chart-donut"></div>
        </el-card>
        <el-card shadow="never" class="source-card">
          <template #header>
            <span>信源分布</span>
            <span class="card-hint">点击下钻</span>
          </template>
          <div v-for="item in metrics?.metrics.source_distribution.items ?? []" :key="item.source_type" class="source-row" @click="drillToSource(item)">
            <span class="source-label">{{ item.label }}</span>
            <el-progress :percentage="Math.round(item.ratio * 100)" :show-text="false" class="source-bar" />
            <span class="source-count">{{ item.count }}</span>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 告警 + 词云 -->
    <el-row :gutter="16">
      <el-col :span="14">
        <el-card shadow="never">
          <template #header>
            <span>实时风险预警</span>
            <span class="card-hint">S/A 级高亮 · 可一键认领 · 可跳转处置</span>
          </template>
          <div class="alert-list">
            <div v-for="alert in alerts" :key="alert.alert_id" :class="alertRowClass(alert)">
              <el-tag :type="alert.level === 'S' || alert.level === 'A' ? 'danger' : 'warning'" size="small" effect="dark">
                {{ RISK_LABELS[alert.level] }}
              </el-tag>
              <div class="alert-body">
                <div class="alert-reason">{{ alert.reason }}</div>
                <div class="alert-meta">
                  {{ alert.rule_label }} · {{ formatTime(alert.triggered_at) }}
                  <template v-if="alert.merged_count > 1"> · 已收敛 {{ alert.merged_count }} 次</template>
                </div>
              </div>
              <el-tag :type="statusTagType(alert.status)" size="small" effect="plain">{{ alert.status_label }}</el-tag>
              <el-button
                v-if="canAckAlerts && canClaim(alert)"
                size="small"
                text
                type="primary"
                :loading="acking === alert.alert_id"
                @click.stop="claimAlert(alert)"
              >
                认领
              </el-button>
              <!-- 没有关联事件的告警（主题级敏感词命中）无处可处置，不显示入口 -->
              <el-button
                v-if="canAckAlerts && alert.event_id"
                size="small"
                text
                type="primary"
                @click.stop="drillToDispose(alert)"
              >
                处置
              </el-button>
            </div>
            <el-empty v-if="!alerts.length" description="暂无告警" :image-size="70" />
          </div>
        </el-card>
      </el-col>
      <el-col :span="10">
        <el-card shadow="never">
          <template #header>
            <span>热点词云</span>
            <span class="card-hint">点击词跳转筛选</span>
          </template>
          <div class="word-cloud">
            <span
              v-for="word in wordCloud"
              :key="word.word"
              class="word"
              :style="{ fontSize: `${word.size}px`, color: word.color }"
              :title="`${word.kind === 'keyword' ? '词包词' : '实体'} · 权重 ${word.weight}`"
              @click="drillToWord(word)"
            >
              {{ word.word }}
            </span>
            <el-empty v-if="!wordCloud.length" description="暂无热词" :image-size="70" />
          </div>
          <div class="word-hint">
            热词来自实体抽取与主题词包的统计（后端未做通用分词，见项目 README）
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.topic-select {
  width: 200px;
}

.refresh-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--color-text-secondary);
}

.role-hint {
  margin-left: auto;
  font-size: 13px;
  color: var(--color-text-secondary);
}

.metrics {
  margin-bottom: 16px;
}

.metric-card {
  text-align: center;
}

.metric-card.clickable {
  cursor: pointer;
}

.metric-label {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.metric-value {
  margin: 8px 0 4px;
  font-size: 26px;
  font-weight: 600;
}

.metric-value.negative {
  color: var(--sentiment-negative);
}

.metric-value.danger {
  color: var(--risk-s);
}

.metric-sub {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.card-hint {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
  color: var(--color-text-secondary);
}

.chart {
  width: 100%;
}

.chart-trend {
  height: 300px;
}

.chart-donut {
  height: 240px;
}

.source-card {
  margin-top: 0;
}

.source-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 0;
  cursor: pointer;
}

.source-label {
  width: 64px;
  font-size: 13px;
  color: var(--color-text-secondary);
}

.source-bar {
  flex: 1;
}

.source-count {
  width: 48px;
  font-size: 13px;
  text-align: right;
}

.alert-list {
  max-height: 300px;
  overflow-y: auto;
}

.alert-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 4px;
  border-bottom: 1px solid var(--color-border);
}

.alert-row:last-child {
  border-bottom: none;
}

/* S/A 级整行高亮（FR-38） */
.alert-row-S {
  background: color-mix(in srgb, var(--risk-s) 6%, transparent);
}

.alert-row-A {
  background: color-mix(in srgb, var(--risk-a) 6%, transparent);
}

.alert-body {
  flex: 1;
  min-width: 0;
}

.alert-reason {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.alert-meta {
  margin-top: 2px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.word-cloud {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 14px;
  min-height: 240px;
  padding: 8px 0;
}

.word {
  cursor: pointer;
  line-height: 1.2;
  transition: transform 0.15s ease;
}

.word:hover {
  transform: scale(1.08);
}

.word-hint {
  margin-top: 8px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--color-text-secondary);
}

.el-card {
  margin-bottom: 16px;
}
</style>
