/**
 * 接口集合。
 *
 * 与后端路由一一对应（设计规格 6.1）。骨架阶段只落地已实现的部分，
 * 其余按任务逐步填充——**不要在这里写猜测的接口**，路径写错前端只会拿到 404。
 */
import { http, request } from './client'
import type {
  HealthStatus,
  LoginPayload,
  PageQuery,
  PageResult,
  ReadyStatus,
  RiskLevel,
  Sentiment,
  SourceType,
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

/** 舆情动态流的多维筛选参数（FR-43） */
export interface ContentQuery extends PageQuery {
  topic_id?: string
  source_type?: SourceType
  sentiment?: Sentiment
  risk_level?: RiskLevel
  start_time?: number
  end_time?: number
  keyword?: string
}

/** 舆情内容（后端 T-22 实现） */
export const contentApi = {
  list: (params: ContentQuery) => http.get<PageResult<unknown>>('/contents', params),
  detail: (contentUid: string) => http.get<unknown>(`/contents/${encodeURIComponent(contentUid)}`),
}

/** 态势大盘（后端 T-26 实现） */
export const dashboardApi = {
  metrics: (params: { topic_id?: string; hours?: number } = {}) =>
    http.get<unknown>('/dashboard/metrics', params),
}

/** 事件专题（后端 T-23 实现） */
export const eventApi = {
  list: (params: PageQuery & { topic_id?: string; risk_level?: RiskLevel } = {}) =>
    http.get<PageResult<unknown>>('/events', params),
  detail: (eventId: string) => http.get<unknown>(`/events/${eventId}`),
  timeline: (eventId: string) => http.get<unknown>(`/events/${eventId}/timeline`),
  clusters: (eventId: string) => http.get<unknown>(`/events/${eventId}/clusters`),
  graph: (eventId: string) => http.get<unknown>(`/events/${eventId}/graph`),
}

/** 研判任务（后端 T-16 实现） */
export const researchApi = {
  start: (payload: { query: string; topic_id?: string }) =>
    http.post<{ task_id: string; started: boolean }>('/research', payload),
  result: (taskId: string) => http.get<unknown>(`/research/${taskId}`),
}

/** 报告中心（后端 T-27 实现） */
export const reportApi = {
  list: (params: PageQuery & { topic_id?: string } = {}) =>
    http.get<PageResult<unknown>>('/reports', params),
  generate: (payload: { event_id?: string; topic_id?: string }) =>
    http.post<{ report_id: string }>('/reports', payload),
  preview: (reportId: string) => http.get<unknown>(`/reports/${reportId}`),
  downloadUrl: (reportId: string, fileType: 'pdf' | 'md') =>
    `/api/v1/reports/${reportId}/download?file_type=${fileType}`,
}
