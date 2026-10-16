/**
 * 接口集合。
 *
 * 与后端路由一一对应（设计规格 6.1）。骨架阶段只落地已实现的部分，
 * 其余按任务逐步填充——**不要在这里写猜测的接口**，路径写错前端只会拿到 404。
 */
import { downloadFile, http, request } from './client'
import type {
  AlertItem,
  AlertRule,
  AlertRuleCreatePayload,
  AlertRuleUpdatePayload,
  AlertScanResult,
  AlertStatus,
  ChannelCheckItem,
  ChannelItem,
  ChannelUpdatePayload,
  ContentDetail,
  ContentItem,
  DashboardMetrics,
  EntityGraphView,
  EventSummary,
  HealthStatus,
  KeywordItem,
  LoginPayload,
  OpinionCluster,
  PageQuery,
  PageResult,
  ReadyStatus,
  ReportCreatePayload,
  ReportDetail,
  ReportItem,
  ReportStatus,
  ReportType,
  ResearchStartPayload,
  ResearchStartResult,
  ResearchTaskView,
  ResponseDraft,
  RiskLevel,
  RiskOverridePayload,
  RuntimeConfigView,
  Sentiment,
  SourceType,
  TicketDetail,
  TicketItem,
  TicketStatus,
  TimelineView,
  TopicCreatePayload,
  TopicDetail,
  TopicStatus,
  TopicSummary,
  TopicUpdatePayload,
  UserInfo,
} from './types'

/**
 * 健康检查。这两个接口不在 /api/v1 下（注册在根路径），
 * 因此显式覆盖 baseURL。
 */
export const healthApi = {
  check: () => request<HealthStatus>({ method: 'GET', url: '/healthz', baseURL: '' }),
  ready: () => request<ReadyStatus>({ method: 'GET', url: '/readyz', baseURL: '' }),
}

/** 认证（后端 T-19 实现） */
export const authApi = {
  login: (payload: LoginPayload) => http.post<UserInfo>('/auth/login', payload),
  logout: () => http.post<void>('/auth/logout'),
  currentUser: () => http.get<UserInfo>('/auth/me'),
}

/** 舆情动态流的多维筛选参数（FR-43）。取值与后端 `/contents` 的参数一一对应 */
export interface ContentQuery extends PageQuery {
  topic_id?: string
  source_type?: SourceType
  sentiment?: Sentiment
  risk_level?: RiskLevel
  start_time?: number
  end_time?: number
  keyword?: string
  /** 只看 / 排除疑似水军 */
  is_suspected_bot?: boolean
  /** 只看人工复核过的内容 */
  is_manual_override?: boolean
}

/** 舆情内容（后端 T-22 实现） */
export const contentApi = {
  list: (params: ContentQuery) => http.get<PageResult<ContentItem>>('/contents', params),
  detail: (contentUid: string) =>
    http.get<ContentDetail>(`/contents/${encodeURIComponent(contentUid)}`),
}

/** 态势大盘（后端 T-26 实现） */
export const dashboardApi = {
  metrics: (params: { topic_id?: string; hours?: number } = {}) =>
    http.get<DashboardMetrics>('/dashboard/metrics', params),
}

/** 预警（后端 T-24 实现） */
export const alertApi = {
  list: (params: { status?: AlertStatus; level?: RiskLevel; event_id?: string; page?: number; page_size?: number } = {}) =>
    http.get<PageResult<AlertItem>>('/alerts', params),
  /** 认领 / 关闭告警。关闭（closed）仅公关可执行，失败会返回 AUTH_003 */
  updateStatus: (alertId: string, payload: { status: AlertStatus; note?: string }) =>
    http.put<AlertItem>(`/alerts/${encodeURIComponent(alertId)}`, payload),
  /** 预警规则：列表 / 新建 / 更新（FR-53）。**仅管理员**（`alert_rule:write`） */
  rules: () => http.get<AlertRule[]>('/alerts/rules'),
  createRule: (payload: AlertRuleCreatePayload) =>
    http.post<AlertRule>('/alerts/rules', payload),
  updateRule: (ruleId: string, payload: AlertRuleUpdatePayload) =>
    http.put<AlertRule>(`/alerts/rules/${encodeURIComponent(ruleId)}`, payload),
  /**
   * 立即执行一次扫描（FR-52 的补扫入口）。正常链路是事件驱动的，
   * 这个接口用于存量补扫与演示：`notify=false` 可只看命中情况而不打扰收件人。
   */
  scan: (params: { window_minutes?: number; dedup_minutes?: number; notify?: boolean } = {}) =>
    request<AlertScanResult>({ method: 'POST', url: '/alerts/scan', params }),
}

/** 监测主题（后端 T-21 实现）——大盘与工作台的主题下拉用 */
export const topicApi = {
  list: (params: { status?: TopicStatus; owner_id?: string; keyword?: string;
                   page?: number; page_size?: number } = {}) =>
    http.get<PageResult<TopicSummary>>('/topics', params),
  detail: (topicId: string) =>
    http.get<TopicDetail>(`/topics/${encodeURIComponent(topicId)}`),
  create: (payload: TopicCreatePayload) => http.post<TopicDetail>('/topics', payload),
  /** 改元信息与启停状态（不含词包——见 replaceKeywords 的说明） */
  update: (topicId: string, payload: TopicUpdatePayload) =>
    http.put<TopicDetail>(`/topics/${encodeURIComponent(topicId)}`, payload),
  /** 全量替换词包。改动是「整份配置」，因此提交完整词条列表 */
  replaceKeywords: (topicId: string, keywords: KeywordItem[]) =>
    http.put<TopicDetail>(`/topics/${encodeURIComponent(topicId)}/keywords`, { keywords }),
  /** 全量替换该主题启用的通道（FR-09） */
  replaceChannels: (topicId: string, channelIds: string[]) =>
    http.put<TopicDetail>(`/topics/${encodeURIComponent(topicId)}/channels`,
                          { channel_ids: channelIds }),
}

/** 信源通道（后端 T-21 实现） */
export const channelApi = {
  list: () => http.get<ChannelItem[]>('/channels'),
  /** 启停 / 调采集频率与回溯窗口。**仅管理员**（`channel:write`） */
  update: (channelId: string, payload: ChannelUpdatePayload) =>
    http.put<ChannelItem>(`/channels/${encodeURIComponent(channelId)}`, payload),
  /** 连通性自检（FR-13）：返回库可达性与原始表条数等可核对的事实 */
  check: (params: { channel_id?: string } = {}) =>
    http.get<ChannelCheckItem[]>('/channels/check', params),
}

/** 运行配置热更新（后端 T-21 实现，FR-53）。**仅管理员**（白名单内、不含密钥） */
export const configApi = {
  read: () => http.get<RuntimeConfigView>('/config'),
  /** 扁平 `{键: 新值}`，键必须在白名单内否则整批拒绝；只作用于进程内单例、不写 .env */
  update: (updates: Record<string, unknown>) =>
    http.post<RuntimeConfigView>('/config', { updates }),
}

/** 事件专题（后端 T-23 实现） */
export const eventApi = {
  list: (params: PageQuery & { topic_id?: string; risk_level?: RiskLevel; status?: string } = {}) =>
    http.get<PageResult<EventSummary>>('/events', params),
  detail: (eventId: string) =>
    http.get<EventSummary>(`/events/${encodeURIComponent(eventId)}`),
  /** node_scope=key 只给关键节点（默认），all 给全部内容 */
  timeline: (eventId: string, params: { node_scope?: 'key' | 'all' } = {}) =>
    http.get<TimelineView>(`/events/${encodeURIComponent(eventId)}/timeline`, params),
  clusters: (eventId: string) =>
    http.get<{ event_id: string; total: number; items: OpinionCluster[] }>(
      `/events/${encodeURIComponent(eventId)}/clusters`),
  /** 簇成员下钻：返回结构与动态流一致，可直接复用卡片组件 */
  clusterMembers: (eventId: string, clusterId: string,
                   params: { page?: number; page_size?: number } = {}) =>
    http.get<PageResult<ContentItem>>(
      `/events/${encodeURIComponent(eventId)}/clusters/${encodeURIComponent(clusterId)}/members`,
      params),
  graph: (eventId: string) =>
    http.get<EntityGraphView>(`/events/${encodeURIComponent(eventId)}/graph`),
  /** 风险等级人工复核（FR-24）。**仅分析师**，理由至少 5 字 */
  overrideRiskLevel: (eventId: string, payload: RiskOverridePayload) =>
    http.put<EventSummary>(`/events/${encodeURIComponent(eventId)}/risk-level`, payload),
  /** 生成公关回应草案（FR-51）。**不落库**：生成是探索动作，采纳才写进工单 */
  responseDraft: (eventId: string, payload: { cluster_labels?: string[] } = {}) =>
    http.post<ResponseDraft>(`/events/${encodeURIComponent(eventId)}/response-draft`, payload),
}

/** 处置工单（后端 T-25 实现） */
export const ticketApi = {
  list: (params: { event_id?: string; status?: TicketStatus; assignee_id?: string;
                   page?: number; page_size?: number } = {}) =>
    http.get<PageResult<TicketItem>>('/tickets', params),
  detail: (ticketId: string) =>
    http.get<TicketDetail>(`/tickets/${encodeURIComponent(ticketId)}`),
  create: (payload: { event_id: string; alert_id?: string; assignee_id?: string;
                      risk_level?: RiskLevel; note?: string }) =>
    http.post<TicketDetail>('/tickets', payload),
  /** 流转 / 认领 / 批注。只传 note 即追加批注；关闭（resolved/ignored）**仅公关** */
  update: (ticketId: string, payload: { status?: TicketStatus; assignee_id?: string;
                                        risk_level?: RiskLevel; note?: string }) =>
    http.put<TicketDetail>(`/tickets/${encodeURIComponent(ticketId)}`, payload),
  /** 采纳草案：content 传人工编辑后的最终文本（省略则沿用已生成的） */
  adoptDraft: (ticketId: string, payload: { content?: string; note?: string } = {}) =>
    http.post<TicketDetail>(`/tickets/${encodeURIComponent(ticketId)}/draft`, payload),
}

/** 研判任务（后端 T-16 的接入部分，API-22/23） */
export const researchApi = {
  /**
   * 发起多智能体研判（FR-64）。目标二选一：`query` 或 `topic_id`。
   * **同时只允许一个任务**——已有任务在跑会返回 409。
   */
  start: (payload: ResearchStartPayload) =>
    http.post<ResearchStartResult>('/research', payload),
  /** 任务状态与各角色结果（FR-66）。进度经 SSE 推送，不在这里轮询 */
  task: (taskId: string) =>
    http.get<ResearchTaskView>(`/research/${encodeURIComponent(taskId)}`),
}

/** 报告中心（后端 T-27 实现） */
export const reportApi = {
  /** 类型 / 主题 / 事件 / 状态筛选 + 分页（FR-57），列表**不含正文** */
  list: (params: PageQuery & {
    topic_id?: string
    event_id?: string
    report_type?: ReportType
    status?: ReportStatus
  } = {}) => http.get<PageResult<ReportItem>>('/reports', params),
  /** 发起生成：异步任务，立即 202 返回 `status=generating`（FR-58） */
  generate: (payload: ReportCreatePayload) => http.post<ReportItem>('/reports', payload),
  /** 详情与在线预览，含 Markdown 原文（FR-60） */
  detail: (reportId: string) =>
    http.get<ReportDetail>(`/reports/${encodeURIComponent(reportId)}`),
  /**
   * 导出产物（FR-61）。`md` 为附件、`html` 为自包含页面（打印即得 PDF，D-07）。
   * **没有 pdf**：服务端不做 PDF 渲染，请求它会被明确拒绝并说明替代路径。
   */
  download: (reportId: string, fileType: 'md' | 'html') =>
    downloadFile(`/reports/${encodeURIComponent(reportId)}/download`, { file_type: fileType }),
}
