/**
 * 展示格式化工具。
 *
 * 舆情的数字量级跨度极大——同一屏里既有 3 次转发的普通用户发言，
 * 也有 200 万点赞的 KOL 内容。不做量级压缩，表格会被数字撑爆。
 */

/** 时间戳（秒）→ `MM-DD HH:mm`；跨年时带年份 */
export function formatTime(timestamp?: number | null, withYear = false): string {
  if (!timestamp) return '-'
  const date = new Date(timestamp * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  const base = `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
  return withYear ? `${date.getFullYear()}-${base}` : base
}

/** 时间戳（秒）→ `YYYY-MM-DD HH:mm:ss` */
export function formatDateTime(timestamp?: number | null): string {
  if (!timestamp) return '-'
  const date = new Date(timestamp * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

/** 相对时间：刚刚 / N 分钟前 / N 小时前 / N 天前 */
export function formatRelative(timestamp?: number | null): string {
  if (!timestamp) return '-'
  const diff = Math.floor(Date.now() / 1000) - timestamp
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} 天前`
  return formatTime(timestamp, true)
}

/** 大数压缩：12345 → 1.2万，123456789 → 1.2亿 */
export function formatCount(value?: number | null): string {
  if (value === null || value === undefined) return '-'
  const n = Number(value)
  if (Number.isNaN(n)) return '-'
  if (n < 10_000) return String(n)
  if (n < 100_000_000) return `${(n / 10_000).toFixed(1).replace(/\.0$/, '')}万`
  return `${(n / 100_000_000).toFixed(2).replace(/\.?0+$/, '')}亿`
}

/** 比例（0~1）→ 百分比字符串 */
export function formatPercent(value?: number | null, digits = 1): string {
  if (value === null || value === undefined) return '-'
  return `${(Number(value) * 100).toFixed(digits)}%`
}
