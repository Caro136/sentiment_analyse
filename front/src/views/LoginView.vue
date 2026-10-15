<script setup lang="ts">
/**
 * 登录页。
 *
 * 开发环境下后端认证（T-19）尚未就绪时，auth store 会建立本地会话，
 * 便于查看页面骨架；生产构建中该回退不会生效。
 */
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'

import { ApiError } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const formRef = ref<FormInstance>()
const form = reactive({ username: '', password: '' })

const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function handleSubmit(): Promise<void> {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  try {
    await auth.login({ username: form.username, password: form.password })
    ElMessage.success('登录成功')
    const redirect = (route.query.redirect as string) || '/dashboard'
    await router.replace(redirect)
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '登录失败，请稍后重试')
  }
}
</script>

<template>
  <div class="login-page">
    <el-card class="login-card" shadow="never">
      <div class="title">
        <span class="mark">舆</span>
        <h1>智能舆情分析平台</h1>
        <p>全网多源监测 · 多智能体研判 · 结构化报告</p>
      </div>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        size="large"
        @keyup.enter="handleSubmit"
      >
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" placeholder="请输入用户名" :prefix-icon="'User'" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="请输入密码"
            show-password
            :prefix-icon="'Lock'"
          />
        </el-form-item>
        <el-button
          type="primary"
          class="submit"
          :loading="auth.loading"
          @click="handleSubmit"
        >
          登 录
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.login-page {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1f2937 0%, #334155 55%, #1e3a5f 100%);
}

.login-card {
  width: 380px;
  padding: 8px 12px;
  border-radius: 10px;
}

.title {
  text-align: center;
  margin-bottom: 24px;
}

.mark {
  display: inline-flex;
  width: 44px;
  height: 44px;
  border-radius: 10px;
  background: #3b82f6;
  color: #fff;
  font-size: 22px;
  font-weight: 700;
  align-items: center;
  justify-content: center;
}

.title h1 {
  margin: 12px 0 4px;
  font-size: 19px;
}

.title p {
  margin: 0;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.submit {
  width: 100%;
  margin-top: 4px;
}
</style>
