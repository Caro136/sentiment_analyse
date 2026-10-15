<script setup lang="ts">
/**
 * 报告中心（PRD FR-57~FR-63）。
 *
 * 计划实现：
 *   - 报告列表（周期性简报 / 突发事件专项报告）  FR-57
 *   - 一键生成与进度展示                        FR-58 / FR-62
 *   - 在线预览（Markdown 渲染）                 FR-60
 *   - 导出 Markdown 与 PDF                      FR-61
 *
 * PDF 导出采用浏览器打印（设计规格 D-07）：前端触发 window.print()，
 * 配合 @media print 样式；**不引入服务端渲染依赖**。
 *
 * 后端接口：/reports（T-27 实现）。
 */
import { ref } from 'vue'

const activeTab = ref<'event' | 'periodic'>('event')
</script>

<template>
  <div class="page">
    <el-alert type="info" :closable="false" show-icon class="hint">
      <template #title>
        报告中心骨架已就绪。报告列表、生成进度、在线预览与导出将在 T-27 完成后接入。
      </template>
    </el-alert>

    <el-row :gutter="16">
      <el-col :span="9">
        <el-card shadow="never">
          <template #header>
            <el-radio-group v-model="activeTab" size="small">
              <el-radio-button value="event">突发事件专项</el-radio-button>
              <el-radio-button value="periodic">周期性简报</el-radio-button>
            </el-radio-group>
          </template>
          <el-empty description="待接入报告列表" :image-size="70" />
        </el-card>
      </el-col>

      <el-col :span="15">
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <span>报告预览（FR-60）</span>
              <div class="actions">
                <el-button size="small" disabled>导出 Markdown</el-button>
                <el-button size="small" disabled>导出 PDF</el-button>
              </div>
            </div>
          </template>
          <el-empty description="选择左侧报告以预览" :image-size="70" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.hint {
  margin-bottom: 16px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.actions {
  display: flex;
  gap: 8px;
}
</style>
