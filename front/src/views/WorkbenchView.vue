<script setup lang="ts">
/**
 * 舆情研判工作台（PRD FR-42~FR-51）。
 *
 * 三栏结构（对应 PRD 页面需求）：
 *   左：监测流与筛选区——多维联动筛选 + 舆情卡片流
 *   中：事件分析与溯源区——传播时间线 / 观点聚类 / 实体图谱 / 多模态原文
 *   右：处置协同与应对区——风险复核、工单流转、公关回应草案
 *
 * 计划实现的组件：
 *   EventTimeline（FR-45）、OpinionClusterList（FR-47）、
 *   EntityGraph（FR-46）、ModalityViewer（FR-44）
 *
 * 后端接口：/contents（T-22）、/events/*（T-23）、/tickets（T-25）。
 */
</script>

<template>
  <div class="page">
    <el-alert type="info" :closable="false" show-icon class="hint">
      <template #title>
        研判工作台骨架已就绪。多维筛选、传播溯源、观点聚类、处置协同将在
        T-22~T-25 完成后接入。
      </template>
    </el-alert>

    <el-row :gutter="12">
      <el-col :span="7">
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <span>监测流与筛选（FR-42/43）</span>
            </div>
          </template>
          <el-empty description="待接入筛选条件与卡片流" :image-size="70" />
        </el-card>
      </el-col>

      <el-col :span="10">
        <el-card shadow="never">
          <template #header>事件分析与溯源（FR-44~48）</template>
          <el-tabs model-value="timeline">
            <el-tab-pane label="传播溯源" name="timeline">
              <el-empty description="待接入传播时间线" :image-size="70" />
            </el-tab-pane>
            <el-tab-pane label="观点聚类" name="clusters">
              <el-empty description="待接入观点聚类" :image-size="70" />
            </el-tab-pane>
            <el-tab-pane label="实体图谱" name="graph">
              <el-empty description="待接入实体关系图谱" :image-size="70" />
            </el-tab-pane>
            <el-tab-pane label="原文详情" name="origin">
              <el-empty description="待接入多模态原文视图" :image-size="70" />
            </el-tab-pane>
          </el-tabs>
        </el-card>
      </el-col>

      <el-col :span="7">
        <el-card shadow="never">
          <template #header>处置协同与应对（FR-49~51）</template>
          <el-empty description="待接入工单流转与回应草案" :image-size="70" />
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
</style>
