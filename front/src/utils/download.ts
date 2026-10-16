/**
 * 浏览器侧的文件落地：下载为新文件、或在新标签页里打开（供打印）。
 *
 * 单独成一个模块，是因为这两件事都必须**自己撤销 object URL**——
 * 忘了 `revokeObjectURL` 会让 Blob 一直挂在内存里（一份报告几百 KB，
 * 连着导出几次就能看出来）。把「创建 → 使用 → 回收」写在一处，比每个调用点各自记着可靠。
 */

/** 触发浏览器「另存为」。用 `<a download>` 而不是 `location.href`：后者会导航走当前页 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  // 立刻撤销在部分浏览器会打断下载，交给下一轮事件循环更稳
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

/**
 * 在新标签页打开。
 *
 * 返回 `null` 表示被弹窗拦截（浏览器只在用户手势内才放行 `window.open`）——
 * 调用方要据此退回「下载文件 + 提示手动打开」，而不是静默什么都不发生。
 * `revoke` 由调用方在合适时机执行（打开后立刻撤销会让标签页拿不到内容）。
 */
export function openBlob(blob: Blob): { window: Window | null; revoke: () => void } {
  const url = URL.createObjectURL(blob)
  const opened = window.open(url, '_blank')
  return { window: opened, revoke: () => URL.revokeObjectURL(url) }
}
