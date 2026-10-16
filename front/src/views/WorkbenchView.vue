<script setup lang="ts">
/**
 * 舆情研判工作台（PRD FR-42~FR-51）。
 *
 * 布局（PRD 7.2）：顶部筛选区 → 左栏卡片流 + 右栏事件分析与溯源区
 * → 底部处置协同与应对区（`DisposalPanel`，FR-49~51）。
 *
 * 底部处置区只在**选中事件后**出现：工单是挂在事件上的，没有事件就没有处置对象。
 *
 * **接收大盘的下钻参数**（T-29 约定）：topic_id / sentiment / source_type /
 * risk_level / keyword / start_time / end_time，取值与后端筛选参数同名同值，直接透传。
 * 筛选条件变化时**同步回 URL**（replace，不污染历史）：刷新与分享都能保留筛选状态。
 *
 * **第二种入口：告警指定的处置对象**（T-31）：大盘告警行的「处置」带 `event_id` +
 * `alert_id` 进来，直接把事件区与底部处置区切到该事件，开单时把告警 ID 一起提交
 * （工单因此能回答「为什么处理它」）。`alert_id` 属于某个具体事件，用户手动换事件即作废。
 *
 * 互跳高亮（PRD 交互要点）用一条 `activeUid` 串起来：卡片 ↔ 时间线节点 ↔ 聚类成员，
 * 点任意一处都会在另外两处高亮同一条内容；图谱实体则跳去筛选内容（关键词＝实体名）。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { contentApi, eventApi, topicApi } from '@/api/modules'
import { ApiError } from '@/api/client'
import ContentCard from '@/components/workbench/ContentCard.vue'
import ContentDetailDrawer from '@/components/workbench/ContentDetailDrawer.vue'
import DisposalPanel from '@/components/workbench/DisposalPanel.vue'
import EntityGraph from '@/components/workbench/EntityGraph.vue'
import EventTimeline from '@/components/workbench/EventTimeline.vue'
import OpinionClusterList from '@/components/workbench/OpinionClusterList.vue'
import type {
  ContentItem,
  EntityGraphView,
  EventSummary,
  OpinionCluster,
  RiskLevel,
  Sentiment,
  SourceType,
  TimelineView,
  TopicSummary,
} from '@/api/types'
import { RISK_LABELS, SENTIMENT_LABELS, SOURCE_LABELS, SOURCE_TYPES } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { formatCount, formatDateTime, formatPercent } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const PAGE_SIZE = 20
const SOURCE_OPTIONS = Object.values(SOURCE_TYPES)
const SENTIMENT_OPTIONS: Sentiment[] = ['positive', 'neutral', 'negative']
const RISK_OPTIONS: RiskLevel[] = ['S', 'A', 'B', 'C']
const RANGE_OPTIONS = [
  { label: '近 24 小时', value: 24 },
  { label: '近 7 天', value: 168 },
  { label: '近 30 天', value: 720 },
]

// ---------------------------------------------------------------------------
// 筛选条件（初始值从 URL 的下钻参数来）
// ---------------------------------------------------------------------------

const filters = reactive({
  topic_id: '',
  source_type: '' as SourceType | '',
  sentiment: '' as Sentiment | '',
  risk_level: '' as RiskLevel | '',
  keyword: '',
  hours: 168,
  start_time: undefined as number | undefined,
  end_time: undefined as number | undefined,
  is_suspected_bot: false,
})

const isAnalyst = computed(() => auth.hasRole('analyst'))

/**
 * 下钻带来的**关联参数**（非筛选条件）：指定要看哪个事件、它来自哪条告警。
 * 与筛选分开存，是因为它们的生命周期不同——筛选随用户操作变，关联只在换事件时作废。
 */
const link = reactive({ event_id: '', alert_id: '', from: '' })

const hasActiveFilter = computed(() => Boolean(
  filters.topic_id || filters.source_type || filters.sentiment || filters.risk_level ||
  filters.keyword || filters.start_time || filters.end_time || filters.is_suspected_bot,
))

function applyQueryToFilters(): void {
  const q = route.query
  filters.topic_id = typeof q.topic_id === 'string' ? q.topic_id : ''
  filters.source_type = (typeof q.source_type === 'string' ? q.source_type : '') as SourceType | ''
  filters.sentiment = (typeof q.sentiment === 'string' ? q.sentiment : '') as Sentiment | ''
  filters.risk_level = (typeof q.risk_level === 'string' ? q.risk_level : '') as RiskLevel | ''
  filters.keyword = typeof q.keyword === 'string' ? q.keyword : ''
  filters.start_time = q.start_time ? Number(q.start_time) : undefined
  filters.end_time = q.end_time ? Number(q.end_time) : undefined
  link.event_id = typeof q.event_id === 'string' ? q.event_id : ''
  link.alert_id = typeof q.alert_id === 'string' ? q.alert_id : ''
  link.from = typeof q.from === 'string' ? q.from : ''
}

/** 把当前筛选与关联参数写回 URL。replace 而非 push：筛选不该在浏览器历史里堆一长串 */
function syncFiltersToQuery(): void {
  const query: Record<string, string> = {}
  if (filters.topic_id) query.topic_id = filters.topic_id
  if (filters.source_type) query.source_type = filters.source_type
  if (filters.sentiment) query.sentiment = filters.sentiment
  if (filters.risk_level) query.risk_level = filters.risk_level
  if (filters.keyword) query.keyword = filters.keyword
  if (filters.start_time) query.start_time = String(filters.start_time)
  if (filters.end_time) query.end_time = String(filters.end_time)
  if (filters.is_suspected_bot) query.is_suspected_bot = 'true'
  // 关联参数必须原样带上：同步筛选用了 replace，漏掉它们会让「从告警进入」的关联
  // 在用户改一次筛选后悄悄消失，开出的工单也就丢了来源
  if (link.event_id) query.event_id = link.event_id
  if (link.alert_id) query.alert_id = link.alert_id
  if (link.from) query.from = link.from
  void router.replace({ query })
}

function resetFilters(): void {
  filters.topic_id = ''
  filters.source_type = ''
  filters.sentiment = ''
  filters.risk_level = ''
  filters.keyword = ''
  filters.start_time = undefined
  filters.end_time = undefined
  filters.is_suspected_bot = false
}

// ---------------------------------------------------------------------------
// 卡片流
// ---------------------------------------------------------------------------

const items = ref<ContentItem[]>([])
const total = ref(0)
const page = ref(1)
const loadingContents = ref(false)

async function loadContents(reset = false): Promise<void> {
  if (reset) page.value = 1
  loadingContents.value = true
  try {
    const result = await contentApi.list({
      page: page.value,
      page_size: PAGE_SIZE,
      topic_id: filters.topic_id || undefined,
      source_type: filters.source_type || undefined,
      sentiment: filters.sentiment || undefined,
      risk_level: filters.risk_level || undefined,
      keyword: filters.keyword || undefined,
      start_time: filters.start_time,
      end_time: filters.end_time,
      is_suspected_bot: filters.is_suspected_bot || undefined,
    })
    items.value = reset ? result.items : [...items.value, ...result.items]
    total.value = result.total
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    loadingContents.value = false
  }
}

function loadMore(): void {
  page.value += 1
  void loadContents(false)
}

// ---------------------------------------------------------------------------
// 事件与三视区
// ---------------------------------------------------------------------------

const topics = ref<TopicSummary[]>([])
const events = ref<EventSummary[]>([])
const selectedEventId = ref('')
const timeline = ref<TimelineView | null>(null)
const clusters = ref<OpinionCluster[]>([])
const graph = ref<EntityGraphView | null>(null)
const activeTab = ref<'timeline' | 'clusters' | 'graph'>('timeline')
const timelineScope = ref<'key' | 'all'>('key')
const loadingEvent = ref(false)

const selectedEvent = computed(
  () => events.value.find((event) => event.event_id === selectedEventId.value) ?? null,
)

async function loadTopicsAndEvents(): Promise<void> {
  try {
    const [topicPage, eventPage] = await Promise.all([
      topicApi.list({ status: 'enabled', page_size: 100 }),
      eventApi.list({ page_size: 50 }),
    ])
    topics.value = topicPage.items
    events.value = eventPage.items
    if (!selectedEventId.value && events.value.length) {
      // 默认选第一个事件：空着右侧会让页面看起来像坏了
      selectedEventId.value = events.value[0].event_id
    }
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  }
}

/**
 * 切到 URL 指定的事件（告警「处置」入口带来的）。
 *
 * 指定的项目可能在最近 50 个事件的列表之外（老事件），那就单独取一次详情补进列表：
 * 否则选中态会指向一个列表里不存在的 ID，界面上表现为「事件区空着」而不报错。
 */
async function applyLinkedEvent(): Promise<void> {
  const wanted = link.event_id
  if (!wanted) return
  if (!events.value.some((event) => event.event_id === wanted)) {
    try {
      events.value = [await eventApi.detail(wanted), ...events.value]
    } catch (error) {
      if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
      return
    }
  }
  if (selectedEventId.value !== wanted) {
    selectedEventId.value = wanted
    handleEventChange()
  }
}

async function loadTimeline(): Promise<void> {
  if (!selectedEventId.value) return
  loadingEvent.value = true
  try {
    timeline.value = await eventApi.timeline(selectedEventId.value,
                                             { node_scope: timelineScope.value })
  } catch (error) {
    timeline.value = null
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    loadingEvent.value = false
  }
}

async function loadClusters(): Promise<void> {
  if (!selectedEventId.value) return
  loadingEvent.value = true
  try {
    clusters.value = (await eventApi.clusters(selectedEventId.value)).items
  } catch (error) {
    clusters.value = []
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    loadingEvent.value = false
  }
}

async function loadGraph(): Promise<void> {
  if (!selectedEventId.value) return
  loadingEvent.value = true
  try {
    graph.value = await eventApi.graph(selectedEventId.value)
  } catch (error) {
    graph.value = null
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    loadingEvent.value = false
  }
}

/** 切标签页时才拉该视区的数据：一次进来三个接口都打没有意义 */
function handleTabChange(name: string): void {
  if (name === 'clusters' && !clusters.value.length) void loadClusters()
  if (name === 'graph' && !graph.value) void loadGraph()
}

function handleEventChange(): void {
  timeline.value = null
  clusters.value = []
  graph.value = null
  void loadTimeline()
  if (activeTab.value === 'clusters') void loadClusters()
  if (activeTab.value === 'graph') void loadGraph()
}

/**
 * 用户手动切换事件。来源告警属于原来那个事件，**切换即作废**——
 * 留着它会让新事件开出的工单挂上一条无关的告警，比丢掉关联更糟。
 */
function onEventSelect(): void {
  if (selectedEventId.value !== link.event_id) {
    link.event_id = selectedEventId.value
    link.alert_id = ''
    link.from = ''
    syncFiltersToQuery()
  }
  handleEventChange()
}

function handleScope(scope: 'key' | 'all'): void {
  timelineScope.value = scope
  void loadTimeline()
}

// ---------------------------------------------------------------------------
// 详情与互跳高亮
// ---------------------------------------------------------------------------

const activeUid = ref<string | null>(null)

/**
 * 当前查看的内容是否就在卡片流里。
 *
 * 时间线与聚类覆盖的是**事件的全部内容**，而卡片流可能被筛选条件与分页限制
 * （实测：按「负向」筛后点时间线节点，卡片流里没有这条 → 高亮看起来像失效）。
 * 不在时就如实说明来源，而不是让用户以为高亮坏了。
 */
const activeInList = computed(
  () => !activeUid.value || items.value.some((item) => item.content_uid === activeUid.value),
)

function openDetail(uid: string): void {
  activeUid.value = uid
}

/** 图谱实体 → 用实体名当关键词筛选内容（FR-46 的「点击实体跳转筛选」） */
function filterByEntity(name: string): void {
  filters.keyword = name
  ElMessage.info(`已按实体「${name}」筛选内容`)
}

// ---------------------------------------------------------------------------
// 风险复核（FR-24，仅分析师）
// ---------------------------------------------------------------------------

const overrideLevel = ref<RiskLevel | ''>('')
const overrideReason = ref('')
const overriding = ref(false)

async function submitOverride(): Promise<void> {
  const event = selectedEvent.value
  if (!event || !overrideLevel.value) return
  if (overrideReason.value.trim().length < 5) {
    ElMessage.warning('复核理由至少 5 个字')
    return
  }
  overriding.value = true
  try {
    const updated = await eventApi.overrideRiskLevel(event.event_id, {
      risk_level: overrideLevel.value as RiskLevel,
      reason: overrideReason.value.trim(),
    })
    const index = events.value.findIndex((item) => item.event_id === updated.event_id)
    if (index >= 0) events.value[index] = updated
    overrideReason.value = ''
    overrideLevel.value = ''
    ElMessage.success('风险等级已复核（算法原始结论仍可查）')
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    overriding.value = false
  }
}

// ---------------------------------------------------------------------------
// 生命周期与联动
// ---------------------------------------------------------------------------

let initialized = false

onMounted(async () => {
  applyQueryToFilters()
  initialized = true
  await Promise.all([loadTopicsAndEvents(), loadContents(true)])
  // 指定了事件就切过去（内部会拉时间线），否则拉默认选中那条
  if (link.event_id) {
    await applyLinkedEvent()
  } else {
    await loadTimeline()
  }
})

// 筛选变化：同步 URL + 重拉列表（关键词输入由回车/失焦触发，避免逐字请求）
watch(
  () => [filters.topic_id, filters.source_type, filters.sentiment, filters.risk_level,
         filters.hours, filters.start_time, filters.end_time, filters.is_suspected_bot],
  () => {
    if (!initialized) return
    syncFiltersToQuery()
    void loadContents(true)
  },
)

// 从大盘再次下钻（同一个组件实例收到新的 query）时，重新读参数
watch(
  () => route.query,
  () => {
    if (route.name !== 'workbench') return
    applyQueryToFilters()
    void loadContents(true)
    // 换了事件（例如从另一条告警点「处置」）时事件区要跟着切
    if (link.event_id && link.event_id !== selectedEventId.value) void applyLinkedEvent()
  },
)
</script>

<template>
  <div class="page">
    <!-- 筛选区（FR-42/43） -->
    <el-card shadow="never" class="filter-card">
      <div class="filters">
        <el-select v-model="filters.topic_id" placeholder="全部主题" clearable class="w-160">
          <el-option v-for="topic in topics" :key="topic.topic_id" :label="topic.name" :value="topic.topic_id" />
        </el-select>

        <el-radio-group v-model="filters.source_type">
          <el-radio-button value="">全部信源</el-radio-button>
          <el-radio-button v-for="source in SOURCE_OPTIONS" :key="source" :value="source">
            {{ SOURCE_LABELS[source] }}
          </el-radio-button>
        </el-radio-group>

        <el-radio-group v-model="filters.sentiment">
          <el-radio-button value="">全部情感</el-radio-button>
          <el-radio-button v-for="sentiment in SENTIMENT_OPTIONS" :key="sentiment" :value="sentiment">
            {{ SENTIMENT_LABELS[sentiment] }}
          </el-radio-button>
        </el-radio-group>

        <el-radio-group v-model="filters.risk_level">
          <el-radio-button value="">全部风险</el-radio-button>
          <el-radio-button v-for="level in RISK_OPTIONS" :key="level" :value="level">
            {{ RISK_LABELS[level] }}
          </el-radio-button>
        </el-radio-group>
      </div>

      <div class="filters second">
        <el-radio-group v-model="filters.hours">
          <el-radio-button v-for="range in RANGE_OPTIONS" :key="range.value" :value="range.value">
            {{ range.label }}
          </el-radio-button>
        </el-radio-group>

        <el-input
          v-model="filters.keyword"
          placeholder="正文关键词，回车搜索"
          clearable
          class="w-220"
          @keyup.enter="loadContents(true)"
          @clear="loadContents(true)"
        />

        <el-checkbox v-model="filters.is_suspected_bot">仅看疑似水军</el-checkbox>

        <el-button :disabled="!hasActiveFilter" @click="resetFilters">重置</el-button>
        <span class="count">
          命中 <b>{{ formatCount(total) }}</b> 条
          <template v-if="filters.start_time || filters.end_time">
            · 时间窗 {{ formatDateTime(filters.start_time) }} ~ {{ formatDateTime(filters.end_time) }}
          </template>
        </span>
      </div>
    </el-card>

    <el-row :gutter="16">
      <!-- 左：卡片流（FR-42/43/44） -->
      <el-col :span="10">
        <el-card shadow="never" class="column-card" v-loading="loadingContents">
          <template #header>
            <span>舆情动态流</span>
            <span class="card-hint">点击卡片看原文与判定依据</span>
          </template>
          <div class="card-list">
            <el-alert
              v-if="!activeInList"
              type="info"
              :closable="false"
              show-icon
              class="outsider-hint"
              title="正在查看的内容来自时间线或聚类，不在当前筛选结果中"
            />
            <ContentCard
              v-for="item in items"
              :key="item.content_uid"
              :item="item"
              :highlight="filters.keyword"
              :active="item.content_uid === activeUid"
              @open="openDetail"
            />
            <el-empty v-if="!items.length && !loadingContents" description="没有命中的内容（试试放宽筛选）" :image-size="80" />
          </div>
          <div v-if="items.length < total" class="load-more">
            <el-button :loading="loadingContents" @click="loadMore">
              加载更多（已显示 {{ items.length }} / {{ total }}）
            </el-button>
          </div>
        </el-card>
      </el-col>

      <!-- 右：事件分析与溯源（FR-45~FR-48） -->
      <el-col :span="14">
        <el-card shadow="never" class="column-card" v-loading="loadingEvent">
          <template #header>
            <div class="event-head">
              <span>事件分析与溯源</span>
              <el-select
                v-model="selectedEventId"
                placeholder="选择事件"
                class="w-260"
                @change="onEventSelect"
              >
                <el-option
                  v-for="event in events"
                  :key="event.event_id"
                  :label="`${event.title}（${event.stats.content_count} 条）`"
                  :value="event.event_id"
                />
              </el-select>
            </div>
          </template>

          <template v-if="selectedEvent">
            <div class="event-stats">
              <span>声量 {{ formatCount(selectedEvent.stats.total_volume) }}</span>
              <span>负面率 {{ formatPercent(selectedEvent.stats.negative_ratio) }}</span>
              <span>
                风险
                <el-tag
                  size="small"
                  effect="dark"
                  :type="selectedEvent.risk_level === 'S' || selectedEvent.risk_level === 'A' ? 'danger' : 'warning'"
                >
                  {{ selectedEvent.risk_level ? RISK_LABELS[selectedEvent.risk_level] : '未判定' }}
                </el-tag>
                <el-tooltip v-if="selectedEvent.is_manual_override" content="人工复核过，算法原始结论仍可查">
                  <span class="algo">算法 {{ selectedEvent.algorithm_risk_level ?? '-' }}·人工</span>
                </el-tooltip>
              </span>
              <span>聚类 {{ selectedEvent.cluster_count }} 簇</span>
              <span>
                时间 {{ formatDateTime(selectedEvent.stats.first_seen_at) }} ~
                {{ formatDateTime(selectedEvent.stats.last_seen_at) }}
              </span>
            </div>

            <p v-if="selectedEvent.override_reason" class="override-reason">
              复核理由：{{ selectedEvent.override_reason }}
            </p>

            <!-- 风险复核（FR-24）：仅分析师 -->
            <div v-if="isAnalyst" class="override-bar">
              <span class="override-label">风险复核</span>
              <el-select v-model="overrideLevel" placeholder="调整等级" clearable class="w-120">
                <el-option v-for="level in RISK_OPTIONS" :key="level" :label="RISK_LABELS[level]" :value="level" />
              </el-select>
              <el-input v-model="overrideReason" placeholder="复核理由（至少 5 字）" class="w-260" />
              <el-button type="primary" :disabled="!overrideLevel" :loading="overriding" @click="submitOverride">
                提交
              </el-button>
            </div>

            <el-tabs v-model="activeTab" class="event-tabs" @tab-change="handleTabChange">
              <el-tab-pane label="传播溯源时间线" name="timeline">
                <EventTimeline :timeline="timeline" :active-uid="activeUid" @open="openDetail" @scope="handleScope" />
              </el-tab-pane>
              <el-tab-pane label="观点聚类" name="clusters">
                <OpinionClusterList :event-id="selectedEventId" :clusters="clusters" @open="openDetail" />
              </el-tab-pane>
              <el-tab-pane label="实体图谱" name="graph">
                <EntityGraph :graph="graph" @focus="filterByEntity" />
              </el-tab-pane>
            </el-tabs>
          </template>

          <el-empty v-else description="暂无可分析的事件（先在监测配置里建主题并接入数据）" :image-size="80" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 从告警处置入口进来时的来源提示：让「为什么突然跳到这个事件」有答案 -->
    <el-alert
      v-if="link.alert_id"
      type="warning"
      :closable="false"
      show-icon
      class="alert-origin"
      :title="`来自告警 ${link.alert_id} 的处置入口`"
      description="开出的工单会关联这条告警；切换事件即解除关联。"
    />

    <!-- 处置协同与应对区（FR-49~51）：选中事件后才有意义 -->
    <DisposalPanel
      v-if="selectedEventId"
      :event-id="selectedEventId"
      :clusters="clusters"
      :alert-id="link.alert_id"
    />

    <!-- 原文详情（FR-44）：抽屉而不是右侧标签页，读全文需要宽度 -->
    <ContentDetailDrawer :uid="activeUid" @close="activeUid = null" />
  </div>
</template>

<style scoped>
.alert-origin {
  margin-top: 16px;
}

.filter-card {
  margin-bottom: 16px;
}

.filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}

.filters.second {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed var(--color-border);
}

.count {
  margin-left: auto;
  font-size: 13px;
  color: var(--color-text-secondary);
}

.count b {
  color: var(--el-color-primary);
}

.column-card {
  min-height: 520px;
}

.card-hint {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
  color: var(--color-text-secondary);
}

.card-list {
  max-height: 560px;
  overflow-y: auto;
  padding-right: 4px;
}

.load-more {
  margin-top: 8px;
  text-align: center;
}

.outsider-hint {
  margin-bottom: 8px;
}

.event-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.event-stats {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 16px;
  padding-bottom: 10px;
  font-size: 13px;
  color: var(--color-text-secondary);
  border-bottom: 1px solid var(--color-border);
}

.algo {
  margin-left: 4px;
  font-size: 12px;
}

.override-reason {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--risk-a);
}

.override-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  padding: 8px 10px;
  background: #f7f9fc;
  border-radius: 6px;
}

.override-label {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.event-tabs {
  margin-top: 8px;
}

.w-120 { width: 120px; }
.w-160 { width: 160px; }
.w-220 { width: 220px; }
.w-260 { width: 260px; }
</style>
