/**
 * 实时事件流（SSE）。
 *
 * 后端通过 `/api/v1/events/stream` 推送五类事件（设计规格 7.2）。
 * 事件只是「运行时可观测性」——推送中断不影响后端研判，
 * 因此这里断线自动重连即可，不需要补偿机制。
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

export interface RealtimeEvent {
  event_type: string
  occurred_at: number
  role?: string
  status?: string
  message?: string
  progress_pct?: number
  section_key?: string | null
  section_title?: string
  message_text?: string
  is_final?: boolean
  error?: string
  [key: string]: unknown
}

/** 后端事件类型（与 engines/contracts/events.py 一致） */
export const EVENT_TYPES = {
  ROLE_PROGRESS: 'role_progress',
  ROLE_RESULT: 'role_result',
  ROLE_ERROR: 'role_error',
  SECTION_READY: 'section_ready',
  HOST_DISCUSSION_MESSAGE: 'host_discussion_message',
} as const

const STREAM_PATH = '/api/v1/events/stream'

/** 重连退避上限（毫秒）。SSE 断线是常态，退避避免频繁重连打爆后端 */
const MAX_RECONNECT_DELAY = 30_000

export const useRealtimeStore = defineStore('realtime', () => {
  const connected = ref(false)
  const lastEventAt = ref<number | null>(null)
  /** 角色 → 进度百分比 */
  const progress = ref<Record<string, number>>({})
  /** 主持人研判消息 */
  const discussion = ref<RealtimeEvent[]>([])
  const errors = ref<RealtimeEvent[]>([])

  let source: EventSource | null = null
  let reconnectDelay = 1_000
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let manualClose = false

  const roleProgress = computed(() => progress.value)

  function handleEvent(event: RealtimeEvent): void {
    lastEventAt.value = Date.now()
    switch (event.event_type) {
      case EVENT_TYPES.ROLE_PROGRESS:
        if (event.role) {
          progress.value[event.role] = event.progress_pct ?? 0
        }
        break
      case EVENT_TYPES.HOST_DISCUSSION_MESSAGE:
        discussion.value.push(event)
        break
      case EVENT_TYPES.ROLE_ERROR:
        errors.value.push(event)
        break
      default:
        break
    }
  }

  function connect(): void {
    if (source) return
    manualClose = false
    source = new EventSource(STREAM_PATH)

    source.onopen = () => {
      connected.value = true
      reconnectDelay = 1_000
    }

    source.onmessage = (message) => {
      try {
        handleEvent(JSON.parse(message.data) as RealtimeEvent)
      } catch {
        // 单条事件解析失败不该中断整个流
      }
    }

    source.onerror = () => {
      connected.value = false
      source?.close()
      source = null
      if (!manualClose) scheduleReconnect()
    }
  }

  function scheduleReconnect(): void {
    if (reconnectTimer) return
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY)
      connect()
    }, reconnectDelay)
  }

  function disconnect(): void {
    manualClose = true
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    source?.close()
    source = null
    connected.value = false
  }

  /** 开始一次新的研判：清空上一轮的进度与讨论 */
  function reset(): void {
    progress.value = {}
    discussion.value = []
    errors.value = []
  }

  return {
    connected,
    lastEventAt,
    progress,
    roleProgress,
    discussion,
    errors,
    connect,
    disconnect,
    reset,
  }
})
