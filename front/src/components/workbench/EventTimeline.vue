<script setup lang="ts">
/**
 * 传播溯源时间线（FR-45，PRD 3.4 的 `EventTimeline`）。
 *
 * 用 `el-timeline` 而不是自绘：节点要显示角色、时间、作者、影响力、摘要，
 * 这些是**结构化的文本信息**，用组件库的时间线可读性更好也更省事。
 *
 * 两条如实展示的约定：
 *   1. 节点角色是**推导**的（后端 `role_source=derived`），面板上要标出来，
 *      避免让人误以为有人工标注；
 *   2. 未打标节点没有情感/风险（为 null），显示为「未标注」而不是空白或默认值。
 */
import { computed } from 'vue'

import RiskTag from '@/components/RiskTag.vue'
import type { TimelineRole, TimelineView } from '@/api/types'
import { SENTIMENT_LABELS, TIMELINE_ROLE_LABELS } from '@/api/types'
import { formatCount, formatDateTime } from '@/utils/format'

const props = defineProps<{ timeline: TimelineView | null; activeUid: string | null }>()
const emit = defineEmits<{
  (event: 'open', uid: string): void
  (event: 'scope', scope: 'key' | 'all'): void
}>()

/** 角色徽标配色：首发/引爆是关注重点，扩散与跟进弱化 */
const ROLE_TAG_TYPES: Record<TimelineRole, 'danger' | 'warning' | 'success' | 'info' | 'primary'> = {
  origin: 'primary',
  ignition: 'danger',
  official: 'success',
  followup: 'warning',
  spread: 'info',
}

const nodes = computed(() => props.timeline?.nodes ?? [])
const scope = computed(() => props.timeline?.node_scope ?? 'key')
</script>

<template>
  <div class="timeline-panel">
    <div class="panel-head">
      <span class="stat">
        共 {{ timeline?.total_contents ?? 0 }} 条内容，展示 {{ timeline?.returned ?? 0 }} 个节点
      </span>
      <el-radio-group :model-value="scope" size="small" @update:model-value="emit('scope', $event as 'key' | 'all')">
        <el-radio-button value="key">关键节点</el-radio-button>
        <el-radio-button value="all">全部内容</el-radio-button>
      </el-radio-group>
    </div>

    <div class="role-summary">
      <el-tag v-for="(count, role) in timeline?.role_summary ?? {}" :key="role" size="small" effect="plain" :type="ROLE_TAG_TYPES[role as TimelineRole] ?? 'info'">
        {{ TIMELINE_ROLE_LABELS[role as TimelineRole] ?? role }} {{ count }}
      </el-tag>
      <span class="derived-note">角色由发布时间与账号权重推导（非人工标注）</span>
    </div>

    <el-timeline class="timeline">
      <el-timeline-item
        v-for="node in nodes"
        :key="node.content_uid"
        :timestamp="formatDateTime(node.published_at)"
        placement="top"
        :type="node.is_key_node ? 'primary' : 'info'"
        :hollow="!node.is_key_node"
      >
        <div :class="['node', { active: node.content_uid === activeUid }]" @click="emit('open', node.content_uid)">
          <div class="node-head">
            <el-tag size="small" effect="dark" :type="ROLE_TAG_TYPES[node.role] ?? 'info'">
              {{ TIMELINE_ROLE_LABELS[node.role] ?? node.role }}
            </el-tag>
            <el-tag size="small" effect="plain">{{ node.platform_label }}</el-tag>
            <span class="author">{{ node.author.nickname }}</span>
            <span v-if="node.author.influence_score" class="influence">
              影响力 {{ node.author.influence_score.toFixed(1) }}
            </span>
            <el-tag v-if="node.author.is_kol" size="small" type="danger" effect="plain">KOL</el-tag>
          </div>
          <div class="node-text">{{ node.text || node.title || '（无正文）' }}</div>
          <div class="node-foot">
            <span v-if="node.sentiment" :class="`tag-sentiment-${node.sentiment}`">
              {{ SENTIMENT_LABELS[node.sentiment] }}
            </span>
            <span v-else class="unlabeled">未标注</span>
            <RiskTag :level="node.risk_level" />
            <span class="engagement">互动 {{ formatCount(node.engagement.total) }}</span>
          </div>
        </div>
      </el-timeline-item>
    </el-timeline>

    <el-empty v-if="!nodes.length" description="该事件暂无溯源节点" :image-size="70" />
  </div>
</template>

<style scoped>
.timeline-panel {
  max-height: 460px;
  overflow-y: auto;
  padding-right: 4px;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.stat {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.role-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
}

.derived-note {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.timeline {
  padding-left: 2px;
}

.node {
  padding: 8px 10px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.node:hover {
  border-color: var(--el-color-primary-light-5);
}

.node.active {
  border-color: var(--el-color-primary);
  box-shadow: 0 0 0 2px var(--el-color-primary-light-8);
}

.node-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}

.author {
  font-weight: 600;
}

.influence {
  color: var(--color-text-secondary);
}

.node-text {
  margin: 6px 0;
  font-size: 13px;
  line-height: 1.65;
}

.node-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.unlabeled {
  color: #a8b0bb;
}
</style>
