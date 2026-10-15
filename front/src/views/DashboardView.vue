<script setup lang="ts">
/**
 * 舆情态势大盘（PRD FR-36~FR-41）。
 *
 * 计划实现：
 *   - 声量趋势折线图          FR-36
 *   - 情感倾向分布环形图      FR-37
 *   - 实时风险预警滚动列表    FR-38
 *   - 热点词云                FR-39
 *   - 核心指标卡片            FR-40
 *   - 按配置周期自动刷新      FR-41
 *
 * 交互约定（设计规格 3.4）：所有图表支持点击下钻到研判工作台并携带筛选条件。
 * 后端接口：GET /api/v1/dashboard/metrics（T-26 实现）。
 */
import { onMounted } from 'vue'

import { useRealtimeStore } from '@/stores/realtime'

const realtime = useRealtimeStore()

onMounted(() => {
  // 骨架阶段先接通实时事件流，验证前后端 SSE 链路是否打通。
  // 后端 SSE 路由在 T-18 实现，未就绪时 store 会自动重连，不影响页面。
  realtime.connect()
})
</script>

<template>
  <div class="page">
    <el-alert type="info" :closable="false" show-icon class="hint">
      <template #title>
        态势大盘骨架已就绪。图表与指标将在 T-26（大盘聚合）完成后接入
        <code>GET /api/v1/dashboard/metrics</code>。
      </template>
    </el-alert>

    <el-row :gutter="16" class="metrics">
      <el-col v-for="label in ['总声量', '负面率', 'S/A 级事件', '待处置工单']" :key="label" :span="6">
        <el-card shadow="never" class="metric-card">
          <div class="metric-label">{{ label }}</div>
          <div class="metric-value">—</div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :span="16">
        <el-card shadow="never">
          <template #header>全网声量趋势（FR-36）</template>
          <el-empty description="待接入 ECharts 折线图" :image-size="80" />
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never">
          <template #header>情感倾向分布（FR-37）</template>
          <el-empty description="待接入环形图" :image-size="80" />
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :span="14">
        <el-card shadow="never">
          <template #header>实时风险预警（FR-38）</template>
          <el-empty description="待接入告警滚动列表" :image-size="80" />
        </el-card>
      </el-col>
      <el-col :span="10">
        <el-card shadow="never">
          <template #header>热点词云（FR-39）</template>
          <el-empty description="待接入词云图" :image-size="80" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.hint {
  margin-bottom: 16px;
}

.metrics {
  margin-bottom: 16px;
}

.metric-card {
  text-align: center;
}

.metric-label {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.metric-value {
  margin-top: 8px;
  font-size: 26px;
  font-weight: 600;
  color: #9aa3af;
}

.el-card {
  margin-bottom: 16px;
}
</style>
