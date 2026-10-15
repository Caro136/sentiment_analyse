/**
 * Axios 封装。
 *
 * 统一承担四件事，业务代码里不必重复处理：
 *   1. 响应解包——调用方直接拿 data，不用到处写 res.data
 *   2. 错误码 → 可读提示（错误码体系见设计规格 6.3）
 *   3. 鉴权失效（AUTH_001/002）自动跳登录，并记住原地址
 *   4. trace_id 透传——报错时能一条 id 串起前后端日志
 */
import axios, { AxiosError, type AxiosInstance, type AxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'

/** 后端统一错误响应体（设计规格 6.3） */
export interface ApiErrorBody {
  code: string
  message: string
  detail?: string
  trace_id?: string
}

/** 错误码段位 → 兜底文案。后端未给出 message 时按段位提示 */
const SEGMENT_HINTS: Record<string, string> = {
  AUTH: '登录状态异常，请重新登录',
  BIZ: '操作未能完成',
  ANA: '分析服务异常，请稍后重试',
  EXT: '外部服务异常，请稍后重试',
  SYS: '系统异常，请联系管理员',
}

/** 把错误码翻译成用户能看懂的一句话 */
export function describeError(body: ApiErrorBody | undefined, fallback: string): string {
  if (!body) return fallback
  if (body.message) return body.message
  const segment = body.code?.split('_')[0] ?? ''
  return SEGMENT_HINTS[segment] ?? fallback
}

/** 业务异常：携带错误码与 trace_id，便于上层按 code 分支处理 */
export class ApiError extends Error {
  readonly code: string
  readonly detail?: string
  readonly traceId?: string
  readonly httpStatus?: number

  constructor(message: string, options: {
    code?: string; detail?: string; traceId?: string; httpStatus?: number
  } = {}) {
    super(message)
    this.name = 'ApiError'
    this.code = options.code ?? 'UNKNOWN'
    this.detail = options.detail
    this.traceId = options.traceId
    this.httpStatus = options.httpStatus
  }

  /** 是否为鉴权类错误 */
  get isAuthError(): boolean {
    return this.code.startsWith('AUTH_') || this.httpStatus === 401
  }
}

const instance: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE ?? '/api/v1',
  timeout: 30_000,
  headers: { 'Content-Type': 'application/json' },
})

// 请求拦截：同源 Cookie 由浏览器自动携带，这里只补充 trace 便于排查
instance.interceptors.request.use((config) => {
  return config
})

// 响应拦截：解包 + 归一化错误
instance.interceptors.response.use(
  (response) => response.data,
  (error: AxiosError<ApiErrorBody>) => {
    // 无响应（网络中断、超时、被取消）
    if (!error.response) {
      const message = error.code === 'ECONNABORTED'
        ? '请求超时，请稍后重试'
        : '网络连接失败，请检查服务是否已启动'
      return Promise.reject(new ApiError(message, { code: 'NETWORK' }))
    }

    const { status, data } = error.response
    const apiError = new ApiError(describeError(data, `请求失败（HTTP ${status}）`), {
      code: data?.code ?? `HTTP_${status}`,
      detail: data?.detail,
      traceId: data?.trace_id,
      httpStatus: status,
    })

    // 鉴权失效：跳登录并记住来源页。这里用动态 import 避免
    // client → router → store → client 的循环依赖。
    if (apiError.isAuthError) {
      void handleAuthFailure()
    } else if (status >= 500) {
      ElMessage.error(apiError.message)
    }

    return Promise.reject(apiError)
  },
)

async function handleAuthFailure(): Promise<void> {
  const { useAuthStore } = await import('@/stores/auth')
  const { default: router } = await import('@/router')

  const auth = useAuthStore()
  auth.clear()

  if (router.currentRoute.value.name !== 'login') {
    ElMessage.warning('登录已失效，请重新登录')
    await router.push({
      name: 'login',
      query: { redirect: router.currentRoute.value.fullPath },
    })
  }
}

/**
 * 统一请求方法。返回已解包的 data，失败抛 ApiError。
 *
 * 这里显式断言返回类型：响应拦截器已经把 AxiosResponse 拆成了 data，
 * 但类型系统看不到这一步，axios 自身泛型在新版本中又发生过变化，
 * 与其跟着版本反复调整，不如在唯一的收口处做一次断言。
 */
export async function request<T>(config: AxiosRequestConfig): Promise<T> {
  const data = await instance.request(config)
  return data as T
}

export const http = {
  // params 用 object 而非 Record<string, unknown>：后者要求显式索引签名，
  // 会让每个查询接口都得加一行 [key: string]: unknown
  get: <T>(url: string, params?: object) =>
    request<T>({ method: 'GET', url, params }),
  post: <T>(url: string, data?: unknown) =>
    request<T>({ method: 'POST', url, data }),
  put: <T>(url: string, data?: unknown) =>
    request<T>({ method: 'PUT', url, data }),
  delete: <T>(url: string, params?: object) =>
    request<T>({ method: 'DELETE', url, params }),
}

export default instance
