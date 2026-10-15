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

export interface UserInfo {
  user_id: string
  username: string
  display_name: string
  role: UserRole
}
