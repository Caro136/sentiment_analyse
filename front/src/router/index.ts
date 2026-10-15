/**
 * 路由与导航守卫。
 *
 * 守卫职责有三：
 *   1. 未登录跳登录页，并记下原地址（登录后能回到原来要看的地方）
 *   2. 已登录访问登录页时重定向到首页（避免重复登录）
 *   3. 角色权限校验——meta.roles 声明该页允许的角色
 *
 * ⚠️ 前端守卫只是体验优化，**真正的权限控制在后端接口**（设计规格 8.1）。
 *    前端隐藏入口挡不住直接调接口，后端必须独立鉴权。
 */
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import type { UserRole } from '@/api/types'
import { useAuthStore } from '@/stores/auth'

declare module 'vue-router' {
  interface RouteMeta {
    /** 是否免登录访问 */
    public?: boolean
    /** 页面标题（用于 document.title 与侧边栏） */
    title?: string
    /** 侧边栏图标名（Element Plus 图标组件名） */
    icon?: string
    /** 允许访问的角色；不声明表示所有已登录用户可访问 */
    roles?: UserRole[]
  }
}

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/',
    component: () => import('@/layouts/DefaultLayout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('@/views/DashboardView.vue'),
        meta: { title: '态势大盘', icon: 'DataLine' },
      },
      {
        path: 'workbench',
        name: 'workbench',
        component: () => import('@/views/WorkbenchView.vue'),
        meta: { title: '研判工作台', icon: 'Search' },
      },
      {
        path: 'reports',
        name: 'reports',
        component: () => import('@/views/ReportsView.vue'),
        meta: { title: '报告中心', icon: 'Document' },
      },
      {
        path: 'config',
        name: 'config',
        component: () => import('@/views/ConfigView.vue'),
        // 监测配置仅管理员与分析师可进（PRD 第 2 章权限矩阵）
        meta: { title: '监测配置', icon: 'SetUp', roles: ['admin', 'analyst'] },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/NotFoundView.vue'),
    meta: { public: true, title: '页面不存在' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  const auth = useAuthStore()

  if (!to.meta.public && !auth.isAuthenticated) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  if (to.name === 'login' && auth.isAuthenticated) {
    return { path: '/dashboard' }
  }

  const required = to.meta.roles
  if (required?.length && !auth.hasRole(...required)) {
    return { path: '/dashboard' }
  }

  return true
})

router.afterEach((to) => {
  document.title = to.meta.title
    ? `${to.meta.title} · 智能舆情分析平台`
    : '智能舆情分析平台'
})

export default router
