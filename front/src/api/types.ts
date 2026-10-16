/**
 * 接口通用类型。
 *
 * 与后端契约对齐，取值必须与 engines/contracts/enums.py 一致——
 * 前端若写错字面值（如 'shortvideo'），筛选会永远返回空结果且不报错。
 */

/** 四类信源 */
export const SOURCE_TYPES = {
  SOCIAL: 'social_media',
  VIDEO: 'short_video',
  NEWS: 'news_portal',
  FORUM: 'industry_forum',
} as const

export type SourceType = (typeof SOURCE_TYPES)[keyof typeof SOURCE_TYPES]

export const SOURCE_LABELS: Record<SourceType, string> = {
  social_media: '社交媒体',
  short_video: '短视频',
  news_portal: '新闻门户',
  industry_forum: '行业论坛',
}

/** 情感倾向 */
export type Sentiment = 'positive' | 'neutral' | 'negative'

export const SENTIMENT_LABELS: Record<Sentiment, string> = {
  positive: '正向',
  neutral: '中性',
  negative: '负向',
}

/** 风险等级 */
export type RiskLevel = 'S' | 'A' | 'B' | 'C'

export const RISK_LABELS: Record<RiskLevel, string> = {
  S: '极高危',
  A: '高危',
  B: '中危',
  C: '低危',
}

/** 角色 —— 与后端 contracts/roles.py 对应 */
export type UserRole = 'analyst' | 'pr' | 'admin'

export const ROLE_LABELS: Record<UserRole, string> = {
  analyst: '舆情分析师',
  pr: '公关运营',
  admin: '系统管理员',
}

/** 分页响应 */
export interface PageResult<T> {
  total: number
  items: T[]
  page: number
  page_size: number
}

/** 分页查询基参 */
export interface PageQuery {
  page?: number
  page_size?: number
}

/** 健康检查 */
export interface HealthStatus {
  status: string
  service?: string
}

export interface ReadyStatus {
  status: 'ok' | 'degraded'
  dependencies: Array<{
    name: string
    target: string
    reachable: boolean
    detail?: string
  }>
  capabilities: Array<{
    name: string
    ready: boolean
    detail: string
  }>
}

/** 登录 */
export interface LoginPayload {
  username: string
  password: string
}

// ---------------------------------------------------------------------------
// 态势大盘（后端 T-26 / app/schemas/dashboard.py）
// ---------------------------------------------------------------------------

/** 核心指标卡片（FR-40）。无数据时 negative_ratio 为 null，不是 0 */
export interface OverviewMetrics {
  total_volume: number
  negative_count: number
  negative_ratio: number | null
  high_risk_content_count: number
  source_count: number
  open_alert_count: number
  high_level_alert_count: number
  high_risk_event_count: number | null
  open_ticket_count: number
  first_analyzed_at: number | null
  last_analyzed_at: number | null
}

/** 趋势桶。后端已补空桶（total=0），因此前端直接连线即可 */
export interface TrendPoint {
  bucket_start: number
  total: number
  negative: number
}

export interface VolumeTrend {
  bucket: string
  bucket_seconds: number
  points: TrendPoint[]
}

export interface SentimentDistribution {
  total: number
  counts: Record<string, number>
  ratios: Record<string, number>
}

export interface SourceDistributionItem {
  source_type: SourceType
  label: string
  count: number
  ratio: number
}

/** 热词。kind 标明来源：entity 实体 / keyword 词包词 */
export interface HotWord {
  word: string
  weight: number
  kind: 'entity' | 'keyword' | string
}

export interface DashboardMetrics {
  scope: string
  topic_id: string | null
  /** 物化时间：前端据此显示「数据更新于 X」并驱动自动刷新（FR-41） */
  computed_at: number
  metrics: {
    overview: OverviewMetrics
    volume_trend: VolumeTrend
    sentiment_distribution: SentimentDistribution
    source_distribution: { total: number; items: SourceDistributionItem[] }
    hot_words: { total_terms: number; items: HotWord[] }
  }
}

// ---------------------------------------------------------------------------
// 预警（后端 T-24 / app/schemas/alert.py）
// ---------------------------------------------------------------------------

export type AlertStatus = 'unread' | 'read' | 'claimed' | 'closed'

/** 预警规则类型（`alert_rule.rule_type`，对应 PRD 附录 B.4 的三个触发条件） */
export type AlertRuleType = 'negative_surge' | 'sensitive_hit' | 'kol_voice'

export const ALERT_RULE_LABELS: Record<AlertRuleType, string> = {
  negative_surge: '负向声量激增',
  sensitive_hit: '敏感词命中',
  kol_voice: '高权重账号发声',
}

/** 阈值的口径**随规则类型而变**：激增是百分比，另两类是条数 */
export const ALERT_RULE_THRESHOLD_HINTS: Record<AlertRuleType, string> = {
  negative_surge: '环比增幅（0.5 = 增长 50%）',
  sensitive_hit: '窗口内命中条数',
  kol_voice: '高权重账号发声条数',
}

export interface AlertRule {
  rule_id: string
  rule_type: AlertRuleType
  type_label: string
  level: RiskLevel
  threshold: number | null
  sensitive_words: string[]
  enabled: boolean
  updated_at: number
}

export interface AlertRuleCreatePayload {
  rule_type: AlertRuleType
  level: RiskLevel
  threshold?: number | null
  sensitive_words?: string[]
  enabled?: boolean
}

export interface AlertRuleUpdatePayload {
  level?: RiskLevel
  threshold?: number | null
  sensitive_words?: string[]
  enabled?: boolean
}

/** 一次预警扫描的结果（FR-52 的补扫入口）。字段给全是为了回答「为什么没报警」 */
export interface AlertScanResult {
  scanned_topics: number
  window_contents: number
  hits: number
  created: string[]
  merged: string[]
  notified: number
  degraded: number
  skipped: number
  summary: string
}

/** 通知投递结果（`alert_record.notify_status`） */
export const NOTIFY_STATUS_LABELS: Record<string, string> = {
  sent: '已发送',
  degraded: '降级（仅站内）',
  skipped: '未发送',
}

export interface AlertItem {
  alert_id: string
  rule_id: string | null
  rule_type: string | null
  rule_label: string
  event_id: string | null
  topic_id: string | null
  level: RiskLevel
  status: AlertStatus
  status_label: string
  triggered_at: number
  responded_at: number | null
  /** 响应时长（秒）。未认领为 null——不能把「没人响应」算成 0 */
  response_seconds: number | null
  merged_count: number
  reason: string
  detail: Record<string, unknown>
  notify_status: string | null
  notify_error: string | null
}

// ---------------------------------------------------------------------------
// 监测主题（后端 T-21 / app/schemas/monitoring.py）
// ---------------------------------------------------------------------------

export interface KeywordGroupStat {
  group_type: KeywordGroup
  label: string
  count: number
}

/** 词包分组（`topic_keyword.group_type`，见 contracts/enums.py） */
export type KeywordGroup = 'brand' | 'event' | 'competitor' | 'exclude'

export const KEYWORD_GROUP_LABELS: Record<KeywordGroup, string> = {
  brand: '品牌词',
  event: '事件词',
  competitor: '竞品词',
  exclude: '排除词',
}

/** 编辑顺序固定：三个召回组在前，排除组收尾（它只负责收窄） */
export const KEYWORD_GROUPS: KeywordGroup[] = ['brand', 'event', 'competitor', 'exclude']

export const KEYWORD_GROUP_HINTS: Record<KeywordGroup, string> = {
  brand: '品牌与产品线名称，召回主力',
  event: '具体事件、争议点',
  competitor: '竞品名称，用于对比观察',
  exclude: '剔除误召回（如品牌词带出的同名噪声）',
}

/**
 * 词包条目。
 *
 * `weight` 用于命中排序（0.1~9.99）。**编辑时必须原样保留**：
 * 词包是全量替换，漏掉权重会静默把所有词重置成 1.0。
 */
export interface KeywordItem {
  word: string
  group_type: KeywordGroup
  weight: number
}

export type TopicStatus = 'enabled' | 'disabled'

export const TOPIC_STATUS_LABELS: Record<TopicStatus, string> = {
  enabled: '启用',
  disabled: '停用',
}

export interface TopicSummary {
  topic_id: string
  name: string
  description: string
  source_keyword: string | null
  owner_id: string
  status: TopicStatus
  start_at: number | null
  created_at: number
  keyword_count: number
  keyword_groups: KeywordGroupStat[]
}

/** 主题详情：带全量词条与关联通道 */
export interface TopicDetail extends TopicSummary {
  keywords: KeywordItem[]
  channel_ids: string[]
}

export interface TopicCreatePayload {
  name: string
  description?: string
  /** 留空时由词包自动推导（见 README「主题关键词是自动推导的」） */
  source_keyword?: string | null
  start_at?: number | null
  status?: TopicStatus
  keywords?: KeywordItem[]
  channel_ids?: string[]
}

export interface TopicUpdatePayload {
  name?: string
  description?: string
  source_keyword?: string | null
  start_at?: number | null
  status?: TopicStatus
}

// ---------------------------------------------------------------------------
// 信源通道与运行配置（后端 T-21 / app/schemas/monitoring.py）
// ---------------------------------------------------------------------------

export interface ChannelItem {
  channel_id: string
  channel_type: SourceType
  type_label: string
  name: string
  enabled: boolean
  poll_interval_min: number
  lookback_hours: number
  last_collected_at: number | null
  last_collected_count: number | null
  updated_at: number
}

/** 通道自检结果（FR-13）。返回的是可核对的事实，不探测外部站点 */
export interface ChannelCheckItem {
  channel_id: string
  channel_type: SourceType
  type_label: string
  name: string
  enabled: boolean
  db_reachable: boolean
  content_table: string
  comment_table: string
  content_rows: number
  comment_rows: number
  latest_published_at: number | null
  last_collected_at: number | null
  last_collected_count: number | null
  message: string
}

export interface ChannelUpdatePayload {
  enabled?: boolean
  poll_interval_min?: number
  lookback_hours?: number
}

/** 白名单内的运行配置项。`overridden` 表示该键被运行时热更新覆盖过 */
export interface RuntimeConfigItem {
  key: string
  value: unknown
  value_type: string
  label: string
  description: string
  overridden: boolean
}

export interface RuntimeConfigView {
  items: RuntimeConfigItem[]
  overridden_keys: string[]
}

// ---------------------------------------------------------------------------
// 舆情内容（后端 T-22 / app/schemas/content.py）
// ---------------------------------------------------------------------------

export interface Engagement {
  likes: number
  comments: number
  shares: number
  collects: number
  replies: number
  total: number
}

export interface AuthorView {
  account_id: string
  nickname: string
  follower_count: number
  verified_type: string
  influence_score: number
  is_kol: boolean
  ip_location: string
}

/** 多模态产物。parse_status=failed 是正常结果之一（ADR-06：解析失败不阻断入库） */
export interface ModalityAsset {
  asset_id: string
  asset_type: string
  parser: string
  parse_status: 'pending' | 'success' | 'failed'
  parsed_text: string
  error_message: string
  origin_url: string
}

export interface ContentItem {
  content_uid: string
  source_type: SourceType
  platform_label: string
  record_type: string
  title: string
  text: string
  /** 列表正文截断到 200 字，详情才给全文 */
  text_truncated: boolean
  url: string
  published_at: number
  author: AuthorView
  engagement: Engagement
  topic_id: string | null
  topic_name: string
  sentiment: Sentiment
  emotion: string | null
  sentiment_confidence: number | null
  risk_level: RiskLevel | null
  intensity_index: number | null
  is_manual_override: boolean
  is_suspected_bot: boolean
}

export interface ContentDetail extends ContentItem {
  raw_text: string
  source_keyword: string
  parent_content_uid: string
  /** 烈度与风险的各因子取值（JSON），用于展示「判定依据」 */
  risk_factors: Record<string, unknown> | null
  override_reason: string | null
  model_version: string
  simhash: string
  analyzed_at: number
  modality_assets: ModalityAsset[]
}

// ---------------------------------------------------------------------------
// 事件与溯源（后端 T-23 / app/schemas/event.py）
// ---------------------------------------------------------------------------

export interface EventStats {
  content_count: number
  post_count: number
  total_volume: number
  negative_count: number
  negative_ratio: number | null
  risk_level: RiskLevel | null
  intensity_index: number | null
  first_seen_at: number | null
  last_seen_at: number | null
  source_distribution: Record<string, number>
  sentiment_distribution: Record<string, number>
}

export interface EventSummary {
  event_id: string
  title: string
  status: 'monitoring' | 'alerting' | 'handling' | 'resolved'
  topic_id: string | null
  topic_name: string
  cluster_count: number
  /** 生效风险（人工复核过即人工值） */
  risk_level: RiskLevel | null
  /** 算法原始结论——两者同时可查（FR-24） */
  algorithm_risk_level: RiskLevel | null
  is_manual_override: boolean
  override_reason: string | null
  stats: EventStats
}

/** 传播角色。取值与后端 contracts.ContentRole / spread_node.node_role 一致 */
export type TimelineRole = 'origin' | 'ignition' | 'official' | 'followup' | 'spread'

/** 注意与上面的 `ROLE_LABELS`（用户角色）区分：这里是**传播角色** */
export const TIMELINE_ROLE_LABELS: Record<TimelineRole, string> = {
  origin: '首发',
  ignition: '引爆',
  official: '官方回应',
  followup: '媒体跟进',
  spread: '扩散',
}

export interface TimelineNode {
  content_uid: string
  published_at: number
  role: TimelineRole
  source_type: SourceType
  platform_label: string
  record_type: string
  author: AuthorView
  title: string
  text: string
  text_truncated: boolean
  url: string
  engagement: Engagement
  /** 未打标的内容为 null——时间线是传播结构，不因缺标签丢节点 */
  sentiment: Sentiment | null
  risk_level: RiskLevel | null
  is_key_node: boolean
}

export interface TimelineView {
  event_id: string
  node_scope: 'key' | 'all'
  ordered_by: string
  /** derived 表示角色由内容与账号权重推导（非人工标注） */
  role_source: string
  total_contents: number
  returned: number
  role_summary: Record<string, number>
  nodes: TimelineNode[]
}

export interface OpinionCluster {
  cluster_id: string
  label: string
  summary: string
  size: number
  sentiment: Sentiment | null
  intent: string | null
  representatives: ContentItem[]
}

export interface GraphNode {
  id: string
  name: string
  /** 实体类型：机构/人物/产品/事件/地点 */
  type: string
}

export interface GraphEdge {
  source: string
  target: string
  type: string
  weight: number
}

export interface EntityGraphView {
  event_id: string
  nodes: GraphNode[]
  edges: GraphEdge[]
  truncated: boolean
}

export interface RiskOverridePayload {
  risk_level: RiskLevel
  /** 复核理由（至少 5 字，后端强制） */
  reason: string
}

// ---------------------------------------------------------------------------
// 处置工单与回应草案（后端 T-25 / app/schemas/ticket.py）
// ---------------------------------------------------------------------------

/** 工单状态：待研判 → 跟踪中 → 已核实 → 辟谣处置 → 已处理 / 已忽略（FR-49） */
export type TicketStatus = 'pending' | 'tracking' | 'verified' | 'debunked' | 'resolved' | 'ignored'

export const TICKET_STATUS_LABELS: Record<TicketStatus, string> = {
  pending: '待研判',
  tracking: '跟踪中',
  verified: '已核实',
  debunked: '辟谣处置',
  resolved: '已处理',
  ignored: '已忽略',
}

export interface TicketLog {
  action: string
  action_label: string
  from_status: TicketStatus | null
  from_status_label: string
  to_status: TicketStatus | null
  to_status_label: string
  operator_id: string
  note: string
  operated_at: number
}

export interface TicketItem {
  ticket_id: string
  event_id: string
  alert_id: string | null
  status: TicketStatus
  status_label: string
  assignee_id: string | null
  risk_level: RiskLevel | null
  has_response_draft: boolean
  created_at: number
  closed_at: number | null
  is_closed: boolean
  /** 当前状态允许流转到的目标（由后端状态机给出，前端不自己维护一份） */
  allowed_transitions: TicketStatus[]
}

export interface TicketDetail extends TicketItem {
  response_draft: string
  logs: TicketLog[]
}

/** 三段式回应草案（FR-51）。**生成不落库**，采纳才写进工单 */
export interface ResponseDraft {
  event_id: string
  title: string
  risk_level: RiskLevel | null
  evidence: {
    total_volume: number
    negative_ratio: number | null
    sentiment_distribution: Record<string, number>
    cluster_labels: string[]
    high_risk_quotes: string[]
    official_statements: string[]
  }
  draft: {
    fact_clarification: string
    emotion_guidance: string
    action_plan: string
    caution: string
  }
  /** 拼好的文本，供编辑器初始化 */
  rendered: string
}

// ---------------------------------------------------------------------------
// 报告中心（后端 T-27 / app/schemas/report.py）
// ---------------------------------------------------------------------------

/** 报告状态（`report.status`） */
export type ReportStatus = 'generating' | 'generated' | 'failed'

export const REPORT_STATUS_LABELS: Record<ReportStatus, string> = {
  generating: '生成中',
  generated: '已生成',
  failed: '生成失败',
}

/** 报告类型：专项报告锚定事件，周期简报锚定主题（生成链路是同一条） */
export type ReportType = 'event' | 'periodic'

export const REPORT_TYPE_LABELS: Record<ReportType, string> = {
  event: '事件专项',
  periodic: '周期简报',
}

export interface ReportFile {
  file_type: string
  /** 磁盘落点，供归档核对；预览与导出不依赖它 */
  file_path: string
  size_bytes: number | null
}

export interface ReportItem {
  report_id: string
  event_id: string | null
  topic_id: string | null
  report_type: ReportType
  type_label: string
  title: string
  status: ReportStatus
  status_label: string
  /** 失败原因（FR-62）。前端据此提示并给「重试」入口 */
  error_message: string | null
  generated_at: number | null
  created_by: string | null
  created_at: number
  /** Markdown 正文字数（列表不含正文，用字数反映规模） */
  chars: number
  files: ReportFile[]
  /** 可导出类型：md / html（html 可打印为 PDF，见 D-07） */
  download_types: string[]
}

export interface ReportDetail extends ReportItem {
  /** Markdown 原文（预览用；正文取自库，不依赖磁盘产物） */
  content_md: string
}

export interface ReportCreatePayload {
  event_id?: string
  topic_id?: string
  /** 显式主题词（与事件/主题三选一）。它同时是报告引擎加载三方产物的键 */
  query?: string
}

export interface UserInfo {
  user_id: string
  username: string
  display_name: string
  role: UserRole
}
