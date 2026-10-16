<script setup lang="ts">
/**
 * 监测配置（PRD 7.4，FR-06~13 / FR-52~54）——配置链路的入口：
 * 「配置主题 → 自动监测 → 主动预警」的第一环，也是唯一能改监测范围的地方。
 *
 * 五个页签（比 PRD 7.4 多一个「运行配置」，理由见下）：
 *   1. 监测主题——主题元信息 + 词包（品牌/事件/竞品/排除）+ 启用通道   FR-06~09
 *   2. 信源通道——四类通道的启停、采集频率、回溯窗口与连通性自检     FR-11~13
 *   3. 预警规则——三类规则的阈值、等级、敏感词库与补扫               FR-52/53
 *   4. 通知渠道——邮件与站内通知的**只读**状态                       FR-54
 *   5. 运行配置——白名单内参数热更新                                 FR-53
 *
 * 五条实现约定：
 *
 * 1. **权限按页签分层，且以角色隐藏而不是禁用**：词包只有分析师（限本人主题）与管理员能改，
 *    通道/规则/运行配置**仅管理员**，公关根本进不来这一页（路由 `meta.roles`）。
 *    隐藏按钮只是体验——后端每个接口都独立鉴权，分析师改别人的主题会直接 403。
 * 2. **词包是「全量替换」**：保存时提交完整词条列表（含权重），不是增量 diff。
 *    因此编辑期必须原样持有后端返回的全部词条与权重（非默认权重会被误重置成 1.0）。
 * 3. **三个写路径分开**：元信息、词包、启用通道各有自己的保存按钮与接口——
 *    合成一个「保存」会让「只改了名字却把词包也提交一遍」变成默认行为。
 * 4. **通知渠道是只读的**：SMTP 主机 / 账号 / 授权码属于密钥与部署配置，
 *    **不在运行配置白名单里**（设计规格 8.1 的刻意约束）。这一页展示能力就绪状态与
 *    最近的投递结果，改配置走 `.env` + 重启，而不是在界面上给一个改密钥的入口。
 * 5. **「运行配置」是本页第五个页签**：PRD 7.4 画了四块，但 FR-53 的白名单热更新
 *    没有别的落点（系统运维页不在前端范围内），而它调的正是监测相关的参数。
 *    它与其它页签的区别也标在界面上：**只作用于进程内、重启回落 `.env`**。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { alertApi, channelApi, configApi, healthApi, topicApi } from '@/api/modules'
import { ApiError } from '@/api/client'
import KeywordGroupEditor from '@/components/config/KeywordGroupEditor.vue'
import type {
  AlertRule,
  AlertRuleType,
  AlertScanResult,
  ChannelCheckItem,
  ChannelItem,
  KeywordItem,
  ReadyStatus,
  RiskLevel,
  RuntimeConfigItem,
  TopicDetail,
  TopicStatus,
  TopicSummary,
} from '@/api/types'
import {
  ALERT_RULE_LABELS,
  ALERT_RULE_THRESHOLD_HINTS,
  KEYWORD_GROUPS,
  KEYWORD_GROUP_HINTS,
  KEYWORD_GROUP_LABELS,
  NOTIFY_STATUS_LABELS,
  RISK_LABELS,
  TOPIC_STATUS_LABELS,
} from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { formatCount, formatDateTime } from '@/utils/format'

const auth = useAuthStore()

/** 通道、规则、运行配置**仅管理员**（PRD 第 2 章）；词包分析师限本人主题 */
const isAdmin = computed(() => auth.hasRole('admin'))
const canWriteTopic = computed(() => auth.hasRole('analyst', 'admin'))

const activeTab = ref('topics')
const RISK_OPTIONS: RiskLevel[] = ['S', 'A', 'B', 'C']

// ---------------------------------------------------------------------------
// 页签一：监测主题与词包（FR-06~09）
// ---------------------------------------------------------------------------

const TOPIC_PAGE_SIZE = 10
const topics = ref<TopicSummary[]>([])
const topicTotal = ref(0)
const topicPage = ref(1)
const topicsLoading = ref(false)
const topicFilters = reactive({ status: '' as TopicStatus | '', keyword: '' })

const selectedTopicId = ref('')
const topicDetail = ref<TopicDetail | null>(null)
const detailLoading = ref(false)
/** 词包草稿：编辑期改的是它，点「保存词包」才提交（全量替换） */
const keywordDraft = ref<KeywordItem[]>([])
const channelIdsDraft = ref<string[]>([])
const channels = ref<ChannelItem[]>([])
const saving = reactive({ meta: false, keywords: false, channels: false })

const metaForm = reactive({
  name: '',
  description: '',
  source_keyword: '',
  start_at: null as number | null,
  status: 'enabled' as TopicStatus,
})

/** 分析师只能改本人创建的主题（后端按数据范围拦，前端同步体现） */
const isOwnTopic = computed(() => !topicDetail.value
  || topicDetail.value.owner_id === auth.user?.user_id)
const canEditTopic = computed(() => isAdmin.value || isOwnTopic.value)

async function loadTopics(): Promise<void> {
  topicsLoading.value = true
  try {
    const page = await topicApi.list({
      page: topicPage.value,
      page_size: TOPIC_PAGE_SIZE,
      status: topicFilters.status || undefined,
      keyword: topicFilters.keyword || undefined,
    })
    topics.value = page.items
    topicTotal.value = page.total
    if (!topics.value.some((item) => item.topic_id === selectedTopicId.value)) {
      await selectTopic(topics.value[0]?.topic_id ?? '')
    }
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    topicsLoading.value = false
  }
}

async function selectTopic(topicId: string): Promise<void> {
  selectedTopicId.value = topicId
  if (!topicId) {
    topicDetail.value = null
    keywordDraft.value = []
    channelIdsDraft.value = []
    return
  }
  detailLoading.value = true
  try {
    const detail = await topicApi.detail(topicId)
    applyDetail(detail)
  } catch (error) {
    topicDetail.value = null
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    detailLoading.value = false
  }
}

/** 详情 → 各表单草稿。三处草稿与详情分开，避免「没点保存却已改到列表上」 */
function applyDetail(detail: TopicDetail): void {
  topicDetail.value = detail
  metaForm.name = detail.name
  metaForm.description = detail.description
  metaForm.source_keyword = detail.source_keyword ?? ''
  metaForm.start_at = detail.start_at
  metaForm.status = detail.status
  keywordDraft.value = detail.keywords.map((item) => ({ ...item }))
  channelIdsDraft.value = [...detail.channel_ids]
}

async function saveMeta(): Promise<void> {
  const detail = topicDetail.value
  if (!detail) return
  saving.meta = true
  try {
    // 传空串 = 清空该字段（后端把空串规整为 None），**不是**「立即重新推导」：
    // 推导发生在保存词包时（当前值为空或等于旧推导值时跟随新词包）。
    // 因此清空后必须再保存一次词包，否则这个主题召不回任何内容。
    // 不能传 undefined——在「省略即不改」的语义里那等于没改。
    applyDetail(await topicApi.update(detail.topic_id, {
      name: metaForm.name.trim(),
      description: metaForm.description.trim(),
      source_keyword: metaForm.source_keyword.trim() || '',
      start_at: metaForm.start_at,
      status: metaForm.status,
    }))
    await loadTopics()
    ElMessage.success('主题信息已保存')
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    saving.meta = false
  }
}

async function saveKeywords(): Promise<void> {
  const detail = topicDetail.value
  if (!detail) return
  saving.keywords = true
  try {
    applyDetail(await topicApi.replaceKeywords(detail.topic_id, keywordDraft.value))
    await loadTopics()
    ElMessage.success('词包已保存（全量替换）')
  } catch (error) {
    // 同词既在召回组又在排除组会被整批拒绝——把后端的原因原样给用户
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    saving.keywords = false
  }
}

async function saveChannels(): Promise<void> {
  const detail = topicDetail.value
  if (!detail) return
  saving.channels = true
  try {
    applyDetail(await topicApi.replaceChannels(detail.topic_id, channelIdsDraft.value))
    ElMessage.success('该主题启用的通道已更新')
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    saving.channels = false
  }
}

// 新建主题
const createVisible = ref(false)
const creating = ref(false)
const createForm = reactive({ name: '', description: '', status: 'enabled' as TopicStatus })

async function submitCreate(): Promise<void> {
  if (!createForm.name.trim()) {
    ElMessage.warning('主题名称不能为空')
    return
  }
  creating.value = true
  try {
    const created = await topicApi.create({
      name: createForm.name.trim(),
      description: createForm.description.trim(),
      status: createForm.status,
    })
    createVisible.value = false
    createForm.name = ''
    createForm.description = ''
    topicPage.value = 1
    await loadTopics()
    await selectTopic(created.topic_id)
    ElMessage.success('主题已创建，接下来补齐词包即可开始监测')
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    creating.value = false
  }
}

watch(() => [topicFilters.status, topicFilters.keyword], () => {
  topicPage.value = 1
  void loadTopics()
})

// ---------------------------------------------------------------------------
// 页签二：信源通道（FR-11~13）
// ---------------------------------------------------------------------------

const channelsLoading = ref(false)
const checks = ref<ChannelCheckItem[]>([])
const checking = ref(false)
/** 通道草稿：每行独立保存，因此按 channel_id 存改过的字段 */
const channelDraft = reactive<Record<string, { poll_interval_min: number; lookback_hours: number }>>({})

function applyChannels(items: ChannelItem[]): void {
  channels.value = items
  for (const item of items) {
    channelDraft[item.channel_id] = {
      poll_interval_min: item.poll_interval_min,
      lookback_hours: item.lookback_hours,
    }
  }
}

async function loadChannels(): Promise<void> {
  channelsLoading.value = true
  try {
    applyChannels(await channelApi.list())
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    channelsLoading.value = false
  }
}

async function toggleChannel(item: ChannelItem): Promise<void> {
  try {
    const updated = await channelApi.update(item.channel_id, { enabled: !item.enabled })
    const index = channels.value.findIndex((row) => row.channel_id === updated.channel_id)
    if (index >= 0) channels.value[index] = updated
    ElMessage.success(`${updated.name}已${updated.enabled ? '启用' : '停用'}`)
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  }
}

async function saveChannelParams(item: ChannelItem): Promise<void> {
  const draft = channelDraft[item.channel_id]
  if (!draft) return
  try {
    const updated = await channelApi.update(item.channel_id, {
      poll_interval_min: draft.poll_interval_min,
      lookback_hours: draft.lookback_hours,
    })
    const index = channels.value.findIndex((row) => row.channel_id === updated.channel_id)
    if (index >= 0) channels.value[index] = updated
    ElMessage.success(`${updated.name}采集参数已保存`)
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  }
}

async function runCheck(): Promise<void> {
  checking.value = true
  try {
    checks.value = await channelApi.check()
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    checking.value = false
  }
}

// ---------------------------------------------------------------------------
// 页签三：预警规则（FR-52/53）
// ---------------------------------------------------------------------------

const rulesLoading = ref(false)
const rules = ref<AlertRule[]>([])
/** 规则草稿：规则行内编辑，保存时只提交改动过的字段 */
const ruleDraft = reactive<Record<string, { level: RiskLevel; threshold: number | null;
                                          sensitive_words: string[]; enabled: boolean }>>({})

function applyRules(items: AlertRule[]): void {
  rules.value = items
  for (const rule of items) {
    ruleDraft[rule.rule_id] = {
      level: rule.level,
      threshold: rule.threshold,
      sensitive_words: [...rule.sensitive_words],
      enabled: rule.enabled,
    }
  }
}

async function loadRules(): Promise<void> {
  rulesLoading.value = true
  try {
    applyRules(await alertApi.rules())
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    rulesLoading.value = false
  }
}

async function saveRule(rule: AlertRule): Promise<void> {
  const draft = ruleDraft[rule.rule_id]
  if (!draft) return
  try {
    const updated = await alertApi.updateRule(rule.rule_id, {
      level: draft.level,
      threshold: draft.threshold,
      sensitive_words: draft.sensitive_words,
      enabled: draft.enabled,
    })
    const index = rules.value.findIndex((row) => row.rule_id === updated.rule_id)
    if (index >= 0) rules.value[index] = updated
    ElMessage.success(`${updated.type_label}规则已保存（下一次扫描生效）`)
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  }
}

const ruleVisible = ref(false)
const creatingRule = ref(false)
const ruleForm = reactive({
  rule_type: 'negative_surge' as AlertRuleType,
  level: 'A' as RiskLevel,
  threshold: 0.5 as number | null,
  sensitive_words: [] as string[],
})

function openRuleDialog(): void {
  ruleForm.rule_type = 'negative_surge'
  ruleForm.level = 'A'
  ruleForm.threshold = 0.5
  ruleForm.sensitive_words = []
  ruleVisible.value = true
}

async function submitRule(): Promise<void> {
  creatingRule.value = true
  try {
    await alertApi.createRule({
      rule_type: ruleForm.rule_type,
      level: ruleForm.level,
      threshold: ruleForm.threshold,
      sensitive_words: ruleForm.sensitive_words,
    })
    ruleVisible.value = false
    await loadRules()
    ElMessage.success('规则已创建（未填阈值时按类型取默认值）')
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    creatingRule.value = false
  }
}

// 补扫（FR-52 的演示与存量补救入口）
const scanVisible = ref(false)
const scanning = ref(false)
const scanForm = reactive({ window_minutes: 60, dedup_minutes: 30, notify: true })
const scanResult = ref<AlertScanResult | null>(null)

async function submitScan(): Promise<void> {
  if (scanForm.notify) {
    try {
      await ElMessageBox.confirm(
        '本次扫描会对命中的规则下发通知（邮件 + 站内）。若只想看命中情况，请取消并关闭「下发通知」。',
        '提示', { type: 'warning', confirmButtonText: '仍要下发', cancelButtonText: '返回' })
    } catch {
      return
    }
  }
  scanning.value = true
  try {
    scanResult.value = await alertApi.scan({ ...scanForm })
    ElMessage.success('扫描完成')
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    scanning.value = false
  }
}

// ---------------------------------------------------------------------------
// 页签四：通知渠道（FR-54，**只读**）
// ---------------------------------------------------------------------------

const ready = ref<ReadyStatus | null>(null)
const readyLoading = ref(false)
const recentAlerts = ref<{ alert_id: string; level: RiskLevel; rule_label: string;
                           notify_status: string | null; notify_error: string | null;
                           triggered_at: number }[]>([])

/** 就绪探测里的通知相关能力项（名字由 `Settings.startup_diagnostics()` 给出） */
const notifyCapabilities = computed(
  () => (ready.value?.capabilities ?? []).filter((item) => item.name.includes('告警')),

)

async function loadNotifyStatus(): Promise<void> {
  readyLoading.value = true
  try {
    // /readyz 是应用根路径下的公开探测接口，因此要显式覆盖 baseURL
    const [status, alerts] = await Promise.all([
      healthApi.ready(),
      alertApi.list({ page_size: 8 }),
    ])
    ready.value = status
    recentAlerts.value = alerts.items.map((item) => ({
      alert_id: item.alert_id,
      level: item.level,
      rule_label: item.rule_label,
      notify_status: item.notify_status,
      notify_error: item.notify_error,
      triggered_at: item.triggered_at,
    }))
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    readyLoading.value = false
  }
}

// ---------------------------------------------------------------------------
// 页签五：运行配置热更新（FR-53，仅管理员）
// ---------------------------------------------------------------------------

const configLoading = ref(false)
const configItems = ref<RuntimeConfigItem[]>([])
const configOverridden = ref<string[]>([])
/** 改过的键 → 新值。只提交改动，避免把没动过的值也写一遍 */
const configDirty = reactive<Record<string, unknown>>({})

const SEARCH_TOOL_OPTIONS = ['bocha', 'tavily', 'anspire']
/** 敏感词库的新增输入框（规则行内共用一份；回车即加入当前规则） */
const newSensitiveWord = ref('')
const LOG_LEVEL_OPTIONS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']

async function loadConfig(): Promise<void> {
  configLoading.value = true
  try {
    const view = await configApi.read()
    configItems.value = view.items
    configOverridden.value = view.overridden_keys
    for (const key of Object.keys(configDirty)) delete configDirty[key]
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    configLoading.value = false
  }
}

function markDirty(key: string, value: unknown): void {
  configDirty[key] = value
}

const dirtyKeys = computed(() => Object.keys(configDirty))

async function submitConfig(): Promise<void> {
  if (!dirtyKeys.value.length) {
    ElMessage.info('没有改动')
    return
  }
  configLoading.value = true
  try {
    const view = await configApi.update({ ...configDirty })
    configItems.value = view.items
    configOverridden.value = view.overridden_keys
    for (const key of Object.keys(configDirty)) delete configDirty[key]
    ElMessage.success('已热更新（只作用于进程内，重启回落到 .env）')
  } catch (error) {
    // 白名单外的键会被整批拒绝并列出可用键——原样展示
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    configLoading.value = false
  }
}

function configOptions(key: string): string[] {
  if (key === 'SEARCH_TOOL_TYPE') return SEARCH_TOOL_OPTIONS
  if (key === 'LOG_LEVEL') return LOG_LEVEL_OPTIONS
  return []
}

function addSensitiveWord(ruleId: string): void {
  const word = newSensitiveWord.value.trim()
  const draft = ruleDraft[ruleId]
  if (!word || !draft) return
  if (!draft.sensitive_words.includes(word)) draft.sensitive_words.push(word)
  newSensitiveWord.value = ''
}

// ---------------------------------------------------------------------------
// 生命周期
// ---------------------------------------------------------------------------

onMounted(async () => {
  // 各页签的数据都先拉：数量都不大（3 主题 / 4 通道 / 3 规则），
  // 换来的是切页签不再有等待感。管理员专属的接口对分析师必然 403，按角色跳过。
  await Promise.all([loadTopics(), loadChannels(), loadNotifyStatus()])
  if (isAdmin.value) {
    await Promise.all([loadRules(), loadConfig()])
  }
})
</script>

<template>
  <div class="page">
    <el-tabs v-model="activeTab" class="config-tabs">
      <!-- 一、监测主题与词包（FR-06~09） -->
      <el-tab-pane label="监测主题" name="topics">
        <el-row :gutter="16">
          <el-col :span="9">
            <el-card shadow="never" class="column-card" v-loading="topicsLoading">
              <template #header>
                <div class="card-head">
                  <span>主题列表</span>
                  <el-button v-if="canWriteTopic" type="primary" size="small" @click="createVisible = true">
                    新建主题
                  </el-button>
                </div>
              </template>

              <div class="filters">
                <el-input
                  v-model="topicFilters.keyword"
                  size="small"
                  placeholder="按名称搜索，回车确认"
                  clearable
                  @keyup.enter="loadTopics"
                  @clear="loadTopics"
                />
                <el-select v-model="topicFilters.status" size="small" placeholder="全部状态"
                           clearable class="w-110">
                  <el-option label="启用" value="enabled" />
                  <el-option label="停用" value="disabled" />
                </el-select>
              </div>

              <div class="topic-list">
                <div
                  v-for="item in topics"
                  :key="item.topic_id"
                  class="topic-item"
                  :class="{ active: item.topic_id === selectedTopicId }"
                  @click="selectTopic(item.topic_id)"
                >
                  <div class="topic-title">
                    <span>{{ item.name }}</span>
                    <el-tag size="small" :type="item.status === 'enabled' ? 'success' : 'info'"
                            effect="plain">
                      {{ TOPIC_STATUS_LABELS[item.status] }}
                    </el-tag>
                  </div>
                  <div class="topic-meta">
                    词包 {{ item.keyword_count }} 个 ·
                    负责人 {{ item.owner_id === auth.user?.user_id ? '本人' : item.owner_id }}
                  </div>
                  <div class="topic-groups">
                    <span v-for="group in item.keyword_groups" :key="group.group_type"
                          class="group-chip">
                      {{ group.label }} {{ group.count }}
                    </span>
                  </div>
                </div>
                <el-empty v-if="!topics.length && !topicsLoading" description="暂无监测主题" :image-size="70" />
              </div>

              <el-pagination
                v-if="topicTotal > TOPIC_PAGE_SIZE"
                v-model:current-page="topicPage"
                :page-size="TOPIC_PAGE_SIZE"
                :total="topicTotal"
                layout="prev, pager, next"
                small
                class="pager"
                @current-change="loadTopics"
              />
            </el-card>
          </el-col>

          <el-col :span="15">
            <el-card shadow="never" class="column-card" v-loading="detailLoading">
              <template #header>
                <div class="card-head">
                  <span>{{ topicDetail ? `编辑：${topicDetail.name}` : '主题与词包编辑' }}</span>
                  <el-tag v-if="topicDetail && !canEditTopic" size="small" type="warning" effect="plain">
                    只读：分析师只能改本人创建的主题
                  </el-tag>
                </div>
              </template>

              <template v-if="topicDetail">
                <!-- 元信息（写路径一） -->
                <el-form label-width="96px" size="small" class="meta-form">
                  <el-form-item label="主题名称">
                    <el-input v-model="metaForm.name" :disabled="!canEditTopic" maxlength="128" />
                  </el-form-item>
                  <el-form-item label="描述">
                    <el-input v-model="metaForm.description" type="textarea" :rows="2"
                              :disabled="!canEditTopic" maxlength="1000" />
                  </el-form-item>
                  <el-form-item label="主题关键词">
                    <el-input v-model="metaForm.source_keyword" :disabled="!canEditTopic"
                              placeholder="接入层靠它召回内容；留空＝清空" />
                    <span class="inline-hint block-hint">
                      建主题时留空会按词包自动推导（品牌词 → 事件词 → 竞品词取权重最高者）。
                      这里清空后再「保存词包」，它会按新词包重新推导；
                      清空后不再保存词包，该主题就召不回任何内容。
                    </span>
                  </el-form-item>
                  <el-form-item label="状态">
                    <el-radio-group v-model="metaForm.status" :disabled="!canEditTopic">
                      <el-radio-button value="enabled">启用</el-radio-button>
                      <el-radio-button value="disabled">停用</el-radio-button>
                    </el-radio-group>
                    <span class="inline-hint">停用后不再召回新内容，已入库数据不受影响</span>
                  </el-form-item>
                  <el-form-item>
                    <el-button type="primary" :disabled="!canEditTopic" :loading="saving.meta"
                               @click="saveMeta">
                      保存主题信息
                    </el-button>
                  </el-form-item>
                </el-form>

                <el-divider content-position="left">词包（写路径二：全量替换）</el-divider>
                <el-alert
                  type="info"
                  :closable="false"
                  show-icon
                  class="mb-10"
                  title="保存时提交整份词包：同一个词既在召回组又在排除组会被后端拒绝（命中与否会取决于匹配顺序）。"
                />
                <KeywordGroupEditor
                  v-for="group in KEYWORD_GROUPS"
                  :key="group"
                  :group="group"
                  :label="KEYWORD_GROUP_LABELS[group]"
                  :hint="KEYWORD_GROUP_HINTS[group]"
                  :items="keywordDraft"
                  :disabled="!canEditTopic"
                  @update:items="keywordDraft = $event"
                />
                <div class="actions">
                  <el-button type="primary" :disabled="!canEditTopic" :loading="saving.keywords"
                             @click="saveKeywords">
                    保存词包（{{ keywordDraft.length }} 个词）
                  </el-button>
                  <span class="inline-hint">权重用于命中排序，0.1~9.99</span>
                </div>

                <el-divider content-position="left">启用通道（写路径三）</el-divider>
                <div class="channel-checks">
                  <el-checkbox-group v-model="channelIdsDraft" :disabled="!canEditTopic">
                    <el-checkbox v-for="channel in channels" :key="channel.channel_id"
                                 :value="channel.channel_id">
                      {{ channel.name }}
                      <span v-if="!channel.enabled" class="inline-hint">（通道已全局停用）</span>
                    </el-checkbox>
                  </el-checkbox-group>
                </div>
                <div class="actions">
                  <el-button :disabled="!canEditTopic" :loading="saving.channels"
                             @click="saveChannels">
                    保存启用通道
                  </el-button>
                  <span class="inline-hint">只决定该主题从哪些通道召回，通道本身的启停见「信源通道」</span>
                </div>
              </template>

              <el-empty v-else description="选择左侧主题以编辑词包" :image-size="80" />
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>

      <!-- 二、信源通道（FR-11~13） -->
      <el-tab-pane label="信源通道" name="channels">
        <el-card shadow="never" v-loading="channelsLoading">
          <template #header>
            <div class="card-head">
              <span>四类信源通道</span>
              <div class="head-actions">
                <span v-if="!isAdmin" class="inline-hint">仅管理员可修改（当前只读）</span>
                <el-button size="small" :loading="checking" @click="runCheck">一键自检</el-button>
              </div>
            </div>
          </template>

          <el-table :data="channels" size="small">
            <el-table-column prop="name" label="通道" width="120" />
            <el-table-column prop="type_label" label="类型" width="110" />
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-switch :model-value="row.enabled" :disabled="!isAdmin"
                           @change="toggleChannel(row)" />
              </template>
            </el-table-column>
            <el-table-column label="轮询间隔（分钟）" width="170">
              <template #default="{ row }">
                <el-input-number v-model="channelDraft[row.channel_id].poll_interval_min"
                                 :min="1" :max="1440" size="small" controls-position="right"
                                 :disabled="!isAdmin" class="w-130" />
              </template>
            </el-table-column>
            <el-table-column label="回溯窗口（小时）" width="170">
              <template #default="{ row }">
                <el-input-number v-model="channelDraft[row.channel_id].lookback_hours"
                                 :min="1" :max="8760" size="small" controls-position="right"
                                 :disabled="!isAdmin" class="w-130" />
              </template>
            </el-table-column>
            <el-table-column label="最近采集" min-width="200">
              <template #default="{ row }">
                <span v-if="row.last_collected_at">
                  {{ formatDateTime(row.last_collected_at) }} ·
                  {{ formatCount(row.last_collected_count ?? 0) }} 条
                </span>
                <span v-else class="inline-hint">尚无采集记录（本项目无真实采集器）</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="100">
              <template #default="{ row }">
                <el-button size="small" text type="primary" :disabled="!isAdmin"
                           @click="saveChannelParams(row)">
                  保存
                </el-button>
              </template>
            </el-table-column>
          </el-table>

          <p class="inline-hint footer-hint">
            通道不由用户增删：四类信源与原始表一一对应，可改的是启停与采集参数。
            停用通道不影响已入库的历史数据。
          </p>

          <template v-if="checks.length">
            <el-divider content-position="left">自检结果（{{ checks.length }} 个通道）</el-divider>
            <p class="inline-hint footer-hint">
              自检返回的是可核对的事实（库可达性、原始表条数、最新发布时间），
              不探测外部站点——本项目没有真实采集器，那种「连通正常」是编出来的。
            </p>
            <el-table :data="checks" size="small">
              <el-table-column prop="name" label="通道" width="120" />
              <el-table-column label="数据库" width="90">
                <template #default="{ row }">
                  <el-tag size="small" :type="row.db_reachable ? 'success' : 'danger'" effect="dark">
                    {{ row.db_reachable ? '可达' : '不可达' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="原始表" min-width="170">
                <template #default="{ row }">
                  <span class="mono">{{ row.content_table }}</span>
                  <span class="inline-hint"> / {{ row.comment_table }}</span>
                </template>
              </el-table-column>
              <el-table-column label="行数（内容/评论）" width="150">
                <template #default="{ row }">
                  {{ formatCount(row.content_rows) }} / {{ formatCount(row.comment_rows) }}
                </template>
              </el-table-column>
              <el-table-column label="最新发布时间" width="170">
                <template #default="{ row }">{{ formatDateTime(row.latest_published_at) }}</template>
              </el-table-column>
              <el-table-column prop="message" label="说明" min-width="220" />
            </el-table>
          </template>
        </el-card>
      </el-tab-pane>

      <!-- 三、预警规则（FR-52/53，仅管理员） -->
      <el-tab-pane v-if="isAdmin" label="预警规则" name="rules">
        <el-card shadow="never" v-loading="rulesLoading">
          <template #header>
            <div class="card-head">
              <span>三类触发规则</span>
              <div class="head-actions">
                <el-button size="small" @click="openRuleDialog">新建规则</el-button>
                <el-button size="small" type="primary" @click="scanVisible = true">立即扫描一次</el-button>
              </div>
            </div>
          </template>

          <el-alert
            type="info"
            :closable="false"
            show-icon
            class="mb-10"
            title="正常链路是事件驱动的：内容打标完成即扫描。下面的「立即扫描」只用于存量补扫与演示。"
          />

          <div v-for="rule in rules" :key="rule.rule_id" class="rule-row">
            <div class="rule-head">
              <span class="rule-name">{{ rule.type_label }}</span>
              <el-switch v-model="ruleDraft[rule.rule_id].enabled" size="small" />
              <span class="inline-hint">
                上次更新 {{ formatDateTime(rule.updated_at) }}
              </span>
            </div>

            <div class="rule-body">
              <span class="rule-label">触发等级</span>
              <el-select v-model="ruleDraft[rule.rule_id].level" size="small" class="w-110">
                <el-option v-for="level in RISK_OPTIONS" :key="level"
                           :label="RISK_LABELS[level]" :value="level" />
              </el-select>

              <span class="rule-label">阈值</span>
              <el-input-number v-model="ruleDraft[rule.rule_id].threshold"
                               :min="0" :max="1000" :step="0.1" :precision="2"
                               size="small" controls-position="right" class="w-130" />
              <span class="inline-hint">{{ ALERT_RULE_THRESHOLD_HINTS[rule.rule_type] }}</span>
            </div>

            <div v-if="rule.rule_type === 'sensitive_hit'" class="rule-words">
              <span class="rule-label">敏感词库</span>
              <div class="words">
                <el-tag
                  v-for="word in ruleDraft[rule.rule_id].sensitive_words"
                  :key="word"
                  size="small"
                  closable
                  @close="ruleDraft[rule.rule_id].sensitive_words =
                          ruleDraft[rule.rule_id].sensitive_words.filter((w) => w !== word)"
                >
                  {{ word }}
                </el-tag>
                <el-input
                  v-model="newSensitiveWord"
                  size="small"
                  class="w-160"
                  placeholder="输入后回车添加"
                  @keyup.enter="addSensitiveWord(rule.rule_id)"
                />
              </div>
            </div>

            <div class="rule-actions">
              <el-button size="small" type="primary" @click="saveRule(rule)">保存规则</el-button>
            </div>
          </div>

          <el-empty v-if="!rules.length && !rulesLoading"
                    description="暂无预警规则（可新建，或跑一次种子脚本）" :image-size="80" />
        </el-card>
      </el-tab-pane>

      <!-- 四、通知渠道（FR-54，只读） -->
      <el-tab-pane label="通知渠道" name="notify">
        <el-card shadow="never" v-loading="readyLoading">
          <template #header>通知能力与近期投递</template>

          <el-alert
            type="warning"
            :closable="false"
            show-icon
            class="mb-10"
            title="本页只读：SMTP 主机 / 账号 / 授权码属于密钥与部署配置，不在运行配置白名单内（设计规格 8.1）。"
          >
            <template #default>
              要改发件邮箱或收件人，请修改 `.env` 的
              <code>SMTP_HOST</code> / <code>SMTP_USER</code> / <code>SMTP_PASSWORD</code> /
              <code>ALERT_MAIL_TO</code> 并重启服务。把改密钥的入口放进界面，
              等于把密钥管理下放给了任何一个管理员账号。
            </template>
          </el-alert>

          <el-divider content-position="left">能力就绪状态（来自 /readyz）</el-divider>
          <el-table :data="notifyCapabilities" size="small">
            <el-table-column prop="name" label="能力" width="140" />
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="row.ready ? 'success' : 'info'" effect="dark">
                  {{ row.ready ? '已配置' : '未配置' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="detail" label="说明" min-width="240" />
          </el-table>
          <p class="inline-hint footer-hint">
            未配置 SMTP 不算失败：通知会降级为站内消息（degraded），
            开发环境不该因为没邮箱就一路报错。
          </p>

          <el-divider content-position="left">近期告警的投递结果</el-divider>
          <p class="inline-hint footer-hint">
            「未记录」表示这条告警创建时没有尝试外发——补扫时选择了不下发通知，
            或当时通知渠道还未配置。想看一次真实的投递结果，可在「预警规则」页签点
            「立即扫描一次」并保持「下发通知」开启（会给收件人真的发一封信）。
          </p>
          <el-table :data="recentAlerts" size="small">
            <el-table-column prop="rule_label" label="触发规则" width="150" />
            <el-table-column label="等级" width="90">
              <template #default="{ row }">{{ RISK_LABELS[row.level as RiskLevel] }}</template>
            </el-table-column>
            <el-table-column label="触发时间" width="170">
              <template #default="{ row }">{{ formatDateTime(row.triggered_at) }}</template>
            </el-table-column>
            <el-table-column label="通知" width="130">
              <template #default="{ row }">
                <el-tag size="small" effect="plain"
                        :type="row.notify_status === 'sent' ? 'success'
                          : row.notify_status === 'degraded' ? 'warning' : 'info'">
                  {{ row.notify_status ? (NOTIFY_STATUS_LABELS[row.notify_status] ?? row.notify_status) : '未记录' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="notify_error" label="投递错误" min-width="200">
              <template #default="{ row }">
                <span v-if="row.notify_error" class="error-text">{{ row.notify_error }}</span>
                <span v-else class="inline-hint">—</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <!-- 五、运行配置（FR-53，仅管理员） -->
      <el-tab-pane v-if="isAdmin" label="运行配置" name="runtime">
        <el-card shadow="never" v-loading="configLoading">
          <template #header>
            <div class="card-head">
              <span>白名单内的可调参数</span>
              <div class="head-actions">
                <span v-if="dirtyKeys.length" class="inline-hint">{{ dirtyKeys.length }} 项待提交</span>
                <el-button size="small" :loading="configLoading" @click="loadConfig">重新读取</el-button>
                <el-button type="primary" size="small" :disabled="!dirtyKeys.length"
                           :loading="configLoading" @click="submitConfig">
                  提交变更
                </el-button>
              </div>
            </div>
          </template>

          <el-alert
            type="warning"
            :closable="false"
            show-icon
            class="mb-10"
            title="热更新立即生效，但只作用于进程内：不写入 .env，重启后回落到环境配置。"
          >
            <template #default>
              需要持久化的业务阈值请用「预警规则」（落在数据库）。
              键不在白名单时整批拒绝而不是静默忽略——静默忽略会让人以为改生效了。
            </template>
          </el-alert>

          <el-table :data="configItems" size="small">
            <el-table-column label="配置项" width="180">
              <template #default="{ row }">
                <div>{{ row.label }}</div>
                <div class="mono inline-hint">{{ row.key }}</div>
              </template>
            </el-table-column>
            <el-table-column label="当前值" width="220">
              <template #default="{ row }">
                <el-switch
                  v-if="row.value_type === 'bool'"
                  :model-value="Boolean(configDirty[row.key] ?? row.value)"
                  @change="(value: string | number | boolean) => markDirty(row.key, Boolean(value))"
                />
                <el-input-number
                  v-else-if="row.value_type === 'int' || row.value_type === 'float'"
                  :model-value="Number(configDirty[row.key] ?? row.value)"
                  :step="row.value_type === 'int' ? 1 : 0.05"
                  :precision="row.value_type === 'int' ? 0 : 2"
                  size="small"
                  controls-position="right"
                  class="w-160"
                  @change="(value: number | undefined) => markDirty(row.key, value ?? row.value)"
                />
                <el-select
                  v-else-if="configOptions(row.key).length"
                  :model-value="String(configDirty[row.key] ?? row.value)"
                  size="small"
                  class="w-160"
                  @change="(value: string) => markDirty(row.key, value)"
                >
                  <el-option v-for="option in configOptions(row.key)" :key="option"
                             :label="option" :value="option" />
                </el-select>
                <el-input
                  v-else
                  :model-value="String(configDirty[row.key] ?? row.value)"
                  size="small"
                  @update:model-value="(value: string) => markDirty(row.key, value)"
                />
              </template>
            </el-table-column>
            <el-table-column label="来源" width="120">
              <template #default="{ row }">
                <el-tag size="small" :type="configOverridden.includes(row.key) ? 'warning' : 'info'"
                        effect="plain">
                  {{ configOverridden.includes(row.key) ? '运行时覆盖' : '.env' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="description" label="说明" min-width="240" />
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 新建主题 -->
    <el-dialog v-model="createVisible" title="新建监测主题" width="480px">
      <el-form label-width="88px" size="small">
        <el-form-item label="名称">
          <el-input v-model="createForm.name" maxlength="128" placeholder="例如：某品牌质量争议" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="createForm.description" type="textarea" :rows="2" maxlength="1000" />
        </el-form-item>
        <el-form-item label="状态">
          <el-radio-group v-model="createForm.status">
            <el-radio-button value="enabled">启用</el-radio-button>
            <el-radio-button value="disabled">停用</el-radio-button>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <p class="inline-hint">
        主题关键词留空即可：建好后在右侧补词包，系统按「品牌词 → 事件词 → 竞品词」
        自动推导出接入层用来归属内容的取值。
      </p>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- 新建预警规则 -->
    <el-dialog v-model="ruleVisible" title="新建预警规则" width="480px">
      <el-form label-width="96px" size="small">
        <el-form-item label="规则类型">
          <el-select v-model="ruleForm.rule_type" class="w-full">
            <el-option v-for="(label, value) in ALERT_RULE_LABELS" :key="value"
                       :label="label" :value="value" />
          </el-select>
        </el-form-item>
        <el-form-item label="触发等级">
          <el-select v-model="ruleForm.level" class="w-full">
            <el-option v-for="level in RISK_OPTIONS" :key="level"
                       :label="RISK_LABELS[level]" :value="level" />
          </el-select>
        </el-form-item>
        <el-form-item label="阈值">
          <el-input-number v-model="ruleForm.threshold" :min="0" :max="1000" :step="0.1"
                           :precision="2" controls-position="right" class="w-full" />
        </el-form-item>
        <el-form-item v-if="ruleForm.rule_type === 'sensitive_hit'" label="敏感词库">
          <el-select v-model="ruleForm.sensitive_words" multiple filterable allow-create
                     default-first-option placeholder="输入后回车添加" class="w-full" />
        </el-form-item>
      </el-form>
      <p class="inline-hint">
        阈值口径随类型而变：{{ ALERT_RULE_THRESHOLD_HINTS[ruleForm.rule_type] }}。
        与已有规则同类型时可以只改阈值，不必新建。
      </p>
      <template #footer>
        <el-button @click="ruleVisible = false">取消</el-button>
        <el-button type="primary" :loading="creatingRule" @click="submitRule">创建</el-button>
      </template>
    </el-dialog>

    <!-- 立即扫描 -->
    <el-dialog v-model="scanVisible" title="立即执行一次预警扫描" width="560px">
      <el-form label-width="110px" size="small">
        <el-form-item label="扫描窗口（分钟）">
          <el-input-number v-model="scanForm.window_minutes" :min="1" :max="1440"
                           controls-position="right" class="w-full" />
        </el-form-item>
        <el-form-item label="收敛窗口（分钟）">
          <el-input-number v-model="scanForm.dedup_minutes" :min="1" :max="1440"
                           controls-position="right" class="w-full" />
        </el-form-item>
        <el-form-item label="下发通知">
          <el-switch v-model="scanForm.notify" />
          <span class="inline-hint">关闭后只落库不通知，适合演示与排障</span>
        </el-form-item>
      </el-form>

      <template v-if="scanResult">
        <el-divider content-position="left">本次扫描结果</el-divider>
        <el-descriptions :column="2" size="small" border>
          <el-descriptions-item label="扫描主题">{{ scanResult.scanned_topics }}</el-descriptions-item>
          <el-descriptions-item label="窗口内内容">{{ scanResult.window_contents }}</el-descriptions-item>
          <el-descriptions-item label="命中">{{ scanResult.hits }}</el-descriptions-item>
          <el-descriptions-item label="新建告警">{{ scanResult.created.length }}</el-descriptions-item>
          <el-descriptions-item label="收敛合并">{{ scanResult.merged.length }}</el-descriptions-item>
          <el-descriptions-item label="已通知">{{ scanResult.notified }}</el-descriptions-item>
          <el-descriptions-item label="降级（仅站内）">{{ scanResult.degraded }}</el-descriptions-item>
          <el-descriptions-item label="未发送">{{ scanResult.skipped }}</el-descriptions-item>
        </el-descriptions>
        <p class="inline-hint footer-hint">{{ scanResult.summary }}</p>
      </template>

      <template #footer>
        <el-button @click="scanVisible = false">关闭</el-button>
        <el-button type="primary" :loading="scanning" @click="submitScan">开始扫描</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.config-tabs :deep(.el-tabs__content) {
  overflow: visible;
}

.column-card {
  min-height: 620px;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.filters {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
}

.topic-list {
  min-height: 440px;
}

.topic-item {
  padding: 8px 10px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  cursor: pointer;
}

.topic-item + .topic-item {
  margin-top: 8px;
}

.topic-item:hover {
  border-color: var(--el-color-primary-light-5);
}

.topic-item.active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.topic-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 14px;
  font-weight: 600;
}

.topic-meta {
  margin-top: 4px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.topic-groups {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 6px;
}

.group-chip {
  padding: 1px 6px;
  font-size: 11px;
  color: var(--color-text-secondary);
  background: #f4f6f9;
  border-radius: 4px;
}

.pager {
  margin-top: 10px;
  justify-content: center;
}

.meta-form {
  max-width: 620px;
}

.actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 12px;
}

.inline-hint {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.block-hint {
  display: block;
  margin-left: 0;
  line-height: 1.7;
}

.footer-hint {
  margin: 8px 0 0;
  line-height: 1.7;
}

.mb-10 {
  margin-bottom: 10px;
}

.channel-checks {
  margin-top: 4px;
}

.mono {
  font-family: Consolas, 'Courier New', monospace;
}

.error-text {
  font-size: 12px;
  color: var(--risk-a);
}

.rule-row {
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
}

.rule-row + .rule-row {
  margin-top: 10px;
}

.rule-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.rule-name {
  font-size: 14px;
  font-weight: 600;
}

.rule-body {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.rule-label {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.rule-words {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-top: 8px;
}

.words {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.rule-actions {
  margin-top: 10px;
}

.w-110 {
  width: 110px;
}

.w-130 {
  width: 130px;
}

.w-160 {
  width: 160px;
}

.w-full {
  width: 100%;
}
</style>
