<script setup lang="ts">
/**
 * 主布局：侧边导航 + 顶栏 + 内容区。
 *
 * 菜单从路由表派生而非单独维护一份——两处维护迟早会出现
 * 「菜单里有但路由没配」或「路由加了菜单忘了加」。
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'

import { useAuthStore } from '@/stores/auth'
import { useRealtimeStore } from '@/stores/realtime'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const realtime = useRealtimeStore()

interface MenuItem {
  path: string
  title: string
  icon: string
}

const menuItems = computed<MenuItem[]>(() => {
  const root = router.options.routes.find((r) => r.path === '/')
  return (root?.children ?? [])
    .filter((child) => {
      const meta = child.meta
      if (!meta?.title) return false
      // 角色不匹配的菜单直接不显示——但真正的拦截在守卫与后端
      if (meta.roles?.length && !auth.hasRole(...meta.roles)) return false
      return true
    })
    .map((child) => ({
      path: `/${child.path}`,
      title: child.meta!.title as string,
      icon: (child.meta!.icon as string) ?? 'Menu',
    }))
})

const activeMenu = computed(() => route.path)
const currentTitle = computed(() => (route.meta.title as string) ?? '')

async function handleCommand(command: string): Promise<void> {
  if (command !== 'logout') return
  try {
    await ElMessageBox.confirm('确认退出登录？', '提示', { type: 'warning' })
  } catch {
    return // 用户取消
  }
  realtime.disconnect()
  await auth.logout()
  await router.push({ name: 'login' })
}
</script>

<template>
  <el-container class="layout">
    <el-aside class="sidebar" :width="'var(--layout-sidebar-width)'">
      <div class="brand">
        <span class="brand-mark">舆</span>
        <span class="brand-text">智能舆情分析</span>
      </div>
      <el-menu :default-active="activeMenu" router class="menu">
        <el-menu-item v-for="item in menuItems" :key="item.path" :index="item.path">
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.title }}</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <div class="header-title">{{ currentTitle }}</div>
        <div class="header-right">
          <el-tooltip
            :content="realtime.connected ? '实时事件流已连接' : '实时事件流未连接（研判进度将不会自动刷新）'"
          >
            <el-tag :type="realtime.connected ? 'success' : 'info'" size="small" effect="plain">
              {{ realtime.connected ? '实时连接' : '未连接' }}
            </el-tag>
          </el-tooltip>
          <el-dropdown @command="handleCommand">
            <span class="user-trigger">
              {{ auth.displayName }}
              <el-icon class="user-arrow"><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item disabled>
                  {{ auth.user?.role }}
                </el-dropdown-item>
                <el-dropdown-item divided command="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.layout {
  height: 100%;
}

.sidebar {
  background: #1f2937;
  display: flex;
  flex-direction: column;
}

.brand {
  height: var(--layout-header-height);
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 16px;
  color: #fff;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.brand-mark {
  width: 26px;
  height: 26px;
  border-radius: 6px;
  background: #3b82f6;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 14px;
}

.brand-text {
  font-size: 15px;
  letter-spacing: 0.5px;
}

.menu {
  flex: 1;
  border-right: none;
  background: transparent;
  --el-menu-bg-color: transparent;
  --el-menu-text-color: #cbd5e1;
  --el-menu-hover-bg-color: rgba(255, 255, 255, 0.06);
  --el-menu-active-color: #fff;
}

.header {
  height: var(--layout-header-height);
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid var(--color-border);
}

.header-title {
  font-size: 16px;
  font-weight: 600;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.user-trigger {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  color: #374151;
  outline: none;
}

.user-arrow {
  font-size: 12px;
}

.main {
  padding: 16px;
  overflow: auto;
}
</style>
