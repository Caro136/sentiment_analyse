/**
 * 认证状态。
 *
 * 会话放在 sessionStorage 而非 localStorage：舆情平台的访问权限通常按值班时段划分，
 * 关掉标签页即失效比"永久记住"更符合使用场景，也降低共享设备上的风险。
 *
 * 真正的凭据是后端下发的 HttpOnly Cookie（前端读不到，也不该读）；
 * sessionStorage 里存的只是**用户信息副本**，用于避免每次导航都请求 `/auth/me`。
 * 因此恢复登录态以 `restore()` 为准：本地没有副本时问后端要。
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { authApi } from '@/api/modules'
import type { LoginPayload, UserInfo, UserRole } from '@/api/types'

const STORAGE_KEY = 'sentiment.auth.session'

function readSession(): UserInfo | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as UserInfo) : null
  } catch {
    return null
  }
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<UserInfo | null>(readSession())
  const loading = ref(false)
  /** 是否已尝试过用 Cookie 恢复会话（避免每次导航都问一次后端） */
  const restoreAttempted = ref(false)

  const isAuthenticated = computed(() => user.value !== null)
  const role = computed<UserRole | undefined>(() => user.value?.role)
  const displayName = computed(() => user.value?.display_name ?? user.value?.username ?? '')

  function persist(info: UserInfo | null): void {
    user.value = info
    if (info) {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(info))
    } else {
      sessionStorage.removeItem(STORAGE_KEY)
    }
  }

  /** 清空本地会话（鉴权失效时由拦截器调用） */
  function clear(): void {
    persist(null)
  }

  /** 判断当前用户是否具备某个角色 */
  function hasRole(...roles: UserRole[]): boolean {
    return !!user.value && roles.includes(user.value.role)
  }

  async function login(payload: LoginPayload): Promise<void> {
    loading.value = true
    try {
      persist(await authApi.login(payload))
      restoreAttempted.value = true
    } finally {
      loading.value = false
    }
  }

  /**
   * 用后端 Cookie 恢复登录态。
   *
   * 场景：刷新或新开标签页后 sessionStorage 为空，但 HttpOnly Cookie 仍有效。
   * 返回是否已登录；只在首次调用时真正请求后端。
   */
  async function restore(): Promise<boolean> {
    if (user.value) return true
    if (restoreAttempted.value) return false
    restoreAttempted.value = true
    try {
      persist(await authApi.currentUser())
      return true
    } catch {
      // 401 是这里的正常路径（没登录或会话已失效），拦截器已处理跳转
      persist(null)
      return false
    }
  }

  async function logout(): Promise<void> {
    try {
      await authApi.logout()
    } catch {
      // 后端不可达时也要让本地登出成功——否则用户会卡在已登录状态出不去
    } finally {
      persist(null)
      // 允许下次进入时再问一次后端，否则登出后换账号登录会因缓存标志而不去恢复
      restoreAttempted.value = false
    }
  }

  return {
    user,
    loading,
    isAuthenticated,
    role,
    displayName,
    login,
    logout,
    restore,
    clear,
    hasRole,
  }
})
