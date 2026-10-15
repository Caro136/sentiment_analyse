/**
 * 认证状态。
 *
 * 会话放在 sessionStorage 而非 localStorage：舆情平台的访问权限通常按值班时段划分，
 * 关掉标签页即失效比"永久记住"更符合使用场景，也降低共享设备上的风险。
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
      const info = await authApi.login(payload)
      persist(info)
    } catch (error) {
      // 开发期后端认证（T-19）尚未就绪时，允许本地建立会话以便查看页面骨架。
      // 生产构建中 import.meta.env.DEV 为 false，这段代码不会生效。
      if (import.meta.env.DEV) {
        persist({
          user_id: 'dev-user',
          username: payload.username,
          display_name: `${payload.username}（开发会话）`,
          role: 'admin',
        })
        return
      }
      throw error
    } finally {
      loading.value = false
    }
  }

  async function logout(): Promise<void> {
    try {
      await authApi.logout()
    } catch {
      // 后端不可达时也要让本地登出成功——否则用户会卡在已登录状态出不去
    } finally {
      persist(null)
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
    clear,
    hasRole,
  }
})
