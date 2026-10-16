<script setup lang="ts">
/**
 * 风险等级标签（PRD 3.4 的复用组件之一）。
 *
 * 颜色取自 `styles/main.css` 的设计令牌——风险等级会出现在卡片、时间线、
 * 聚类、图谱、大屏各处，各自定义必然出现「同一个 S 级在不同页面颜色不同」。
 */
import { computed } from 'vue'

import { RISK_LABELS, type RiskLevel } from '@/api/types'

const props = withDefaults(
  defineProps<{
    level?: RiskLevel | null
    /** 手动复核过的等级加一个标记，与算法结论区分开 */
    manual?: boolean
    size?: 'small' | 'default'
  }>(),
  { level: null, manual: false, size: 'small' },
)

const label = computed(() => (props.level ? RISK_LABELS[props.level] : '未分级'))
</script>

<template>
  <el-tag
    :class="['risk-tag', level ? `tag-risk-${level}` : 'risk-tag-none']"
    :size="size"
    effect="dark"
    disable-transitions
  >
    {{ label }}<span v-if="manual" class="manual-mark" title="经人工复核">·人工</span>
  </el-tag>
</template>

<style scoped>
.risk-tag {
  border: none;
}

.risk-tag-none {
  color: var(--color-text-secondary);
  background: #eef1f5;
}

.manual-mark {
  margin-left: 2px;
  opacity: 0.85;
}
</style>
