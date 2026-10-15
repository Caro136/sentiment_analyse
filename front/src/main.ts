import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'

import 'element-plus/dist/index.css'
import '@/styles/main.css'

import App from '@/App.vue'
import router from '@/router'

const app = createApp(App)

// Pinia 必须先于 router 安装：导航守卫里会使用 store，
// 顺序颠倒会在首次导航时报 "no active Pinia"
app.use(createPinia())
app.use(router)

app.use(ElementPlus, { locale: zhCn })

// 图标全量注册：Element Plus 的图标数量有限，全量注册省去逐个导入的繁琐
for (const [name, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(name, component)
}

app.mount('#app')
