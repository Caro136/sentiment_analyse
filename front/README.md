# 智能舆情分析平台 · 前端

Vue 3 + TypeScript + Vite 单页应用。承接 `docs/智能舆情分析平台-设计规格.md` 的 T-06 与前端模块划分。

## 快速开始

```bash
cd front
npm install            # 已配置国内镜像（.npmrc）

npm run dev            # 开发服务器 http://localhost:5173
npm run type-check     # 类型检查（不改动产物）
npm run build          # 类型检查 + 生产构建，输出 dist/
npm run preview        # 预览构建产物
```

开发服务器已把 `/api` 代理到 `http://localhost:8000`，因此本地起后端后无需处理跨域。

## 目录结构

```
front/
├── index.html
├── vite.config.ts         构建配置（别名、代理、分包）
├── tsconfig.json          TypeScript 严格模式
├── .npmrc                 国内镜像
└── src/
    ├── main.ts            应用装配（Pinia 必须先于 Router 安装）
    ├── App.vue            根组件，仅路由出口
    ├── api/
    │   ├── client.ts      Axios 封装：解包、错误码、鉴权失效处理
    │   ├── types.ts       接口通用类型（与后端契约对齐）
    │   └── modules.ts     接口集合（按后端路由分组）
    ├── router/index.ts    路由表与导航守卫
    ├── stores/
    │   ├── auth.ts        认证状态（会话存 sessionStorage）
    │   └── realtime.ts    SSE 事件流（自动重连）
    ├── layouts/
    │   └── DefaultLayout.vue  侧边导航 + 顶栏（菜单由路由表派生）
    ├── views/
    │   ├── LoginView.vue
    │   ├── DashboardView.vue   态势大盘（FR-36~41）
    │   ├── WorkbenchView.vue   研判工作台（FR-42~51）
    │   ├── ReportsView.vue     报告中心（FR-57~63）
    │   ├── ConfigView.vue      监测配置（FR-06~13）
    │   └── NotFoundView.vue
    ├── utils/format.ts    时间与数字格式化
    └── styles/main.css    设计令牌与全局样式
```

## 与后端的约定

| 约定 | 说明 |
|------|------|
| 接口前缀 | `/api/v1`（在 `client.ts` 的 baseURL 中统一配置） |
| 健康检查 | `/healthz`、`/readyz` 在根路径，**不在** `/api/v1` 下，请求时显式覆盖 baseURL |
| 响应解包 | 拦截器已返回 `data`，调用方直接拿业务数据，不必写 `res.data` |
| 错误处理 | 失败抛 `ApiError`（含 `code` / `trace_id`），401 或 `AUTH_*` 自动跳登录 |
| 枚举取值 | 必须与 `engines/contracts/enums.py` 一致，写错字面值不会报错但筛选恒为空 |
| 字段命名 | 后端接口统一用 snake_case（`content_uid` / `risk_level`），前端不做驼峰转换 |

> `/healthz` 与 `/readyz` 的路径差异是个容易踩的点：它们注册在应用根路径，
> 而业务接口在 `/api/v1` 下。axios 的 baseURL 会无条件拼接，所以健康检查必须显式传 `baseURL: ''`。

## 骨架状态

页面目前是**结构占位**：菜单、路由、守卫、状态管理、接口封装、格式化工具、设计令牌都已就位，
图表与业务交互待后端接口完成后逐个接入。每个视图顶部注释标明了它对应的 PRD 功能编号与后端任务号。

已可验证的链路：

- 路由跳转与登录守卫（未登录访问任意页会跳登录页并记住来源）
- 角色权限（`监测配置` 仅管理员与分析师可见，路由 `meta.roles` 控制）
- 登录表单校验与错误提示
- SSE 实时事件流的连接与自动重连（后端 T-18 完成前会持续重连，不影响页面）

## 开发须知

1. **前端守卫挡不住直接调接口**。`meta.roles` 只是体验优化，真正的权限控制必须在后端独立鉴权（设计规格 8.1）。
2. **不要在 `api/modules.ts` 里写猜测的接口**。路径写错前端只会拿到 404，排查成本远高于等后端接口就绪。
3. **菜单不要单独维护**。`DefaultLayout` 从路由表派生菜单，避免「菜单有但路由没配」这类不一致。
4. **图表配色统一取 `styles/main.css` 的设计令牌**。风险等级与情感倾向的颜色会出现在大屏、卡片、标签、图表各处，各自定义必然出现同一等级颜色不一致。

## 依赖版本

主要依赖版本（见 `package.json`）：

| 包 | 版本 | 说明 |
|----|------|------|
| vue | ^3.5 | |
| vue-router | ^5.4 | 注意：5.x 相比 4.x 有破坏性变更 |
| pinia | ^4.1 | 注意：4.x 相比 2.x 有破坏性变更 |
| element-plus | ^2.14 | UI 组件库 |
| echarts | ^6.1 | 图表 |
| axios | ^1.20 | HTTP 客户端 |
