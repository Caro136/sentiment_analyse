<script setup lang="ts">
/**
 * 监测配置（PRD FR-06~FR-13、FR-52~FR-54）。
 *
 * 四个配置区：
 *   监测主题——主题与词包（品牌词 / 事件词 / 竞品词 / 排除词）  FR-06/07
 *   信源通道——四类信源的启停与采集频率                      FR-11/12/13
 *   预警规则——分级阈值与敏感词库                            FR-52/53
 *   通知渠道——邮件收件人配置                                FR-54
 *
 * 权限：仅管理员与分析师可访问（路由 meta.roles 已限制）。
 * 后端接口：/topics、/channels、/config（T-21 实现）。
 */
</script>

<template>
  <div class="page">
    <el-alert type="info" :closable="false" show-icon class="hint">
      <template #title>
        监测配置骨架已就绪。主题词包、信源通道、预警规则与通知渠道将在 T-21 完成后接入。
      </template>
    </el-alert>

    <el-tabs model-value="topics">
      <el-tab-pane label="监测主题" name="topics">
        <el-card shadow="never">
          <el-empty description="待接入主题与词包管理" :image-size="70" />
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="信源通道" name="channels">
        <el-card shadow="never">
          <el-empty description="待接入四类信源通道开关与采集频率" :image-size="70" />
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="预警规则" name="rules">
        <el-card shadow="never">
          <el-empty description="待接入阈值与敏感词配置" :image-size="70" />
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="通知渠道" name="notify">
        <el-card shadow="never">
          <el-empty description="待接入邮件通知配置" :image-size="70" />
        </el-card>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.hint {
  margin-bottom: 16px;
}
</style>
