<script setup lang="ts">
/**
 * 观点聚类列表（FR-47，PRD 3.4 的 `OpinionClusterList`）。
 *
 * 后端已按**簇规模倒序**返回，前端不再排序——排序口径只有一处（后端），
 * 两边各排一次迟早不一致。
 *
 * 每簇展示：标签、规模、主导情感、摘要、代表性言论；点击簇下钻成员
 * （成员接口返回结构与动态流一致，因此点击成员直接复用同一个详情抽屉）。
 */
import { ref } from 'vue'

import RiskTag from '@/components/RiskTag.vue'
import { eventApi } from '@/api/modules'
import { ApiError } from '@/api/client'
import type { ContentItem, OpinionCluster } from '@/api/types'
import { SENTIMENT_LABELS } from '@/api/types'
import { formatCount, formatRelative } from '@/utils/format'

const props = defineProps<{ eventId: string; clusters: OpinionCluster[] }>()
const emit = defineEmits<{ (event: 'open', uid: string): void }>()

/** 展开中的簇 → 成员列表（懒加载：一次展开一次请求，不预取全部簇的成员） */
const expanded = ref<string>('')
const members = ref<Record<string, ContentItem[]>>({})
const memberTotals = ref<Record<string, number>>({})
const loading = ref<string>('')

async function toggle(cluster: OpinionCluster): Promise<void> {
  if (expanded.value === cluster.cluster_id) {
    expanded.value = ''
    return
  }
  expanded.value = cluster.cluster_id
  if (members.value[cluster.cluster_id]) return

  loading.value = cluster.cluster_id
  try {
    const page = await eventApi.clusterMembers(props.eventId, cluster.cluster_id,
                                               { page_size: 10 })
    members.value[cluster.cluster_id] = page.items
    memberTotals.value[cluster.cluster_id] = page.total
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) members.value[cluster.cluster_id] = []
  } finally {
    loading.value = ''
  }
}
</script>

<template>
  <div class="cluster-panel">
    <el-empty v-if="!clusters.length" description="该事件暂无聚类结果" :image-size="70" />
    <div v-for="cluster in clusters" :key="cluster.cluster_id" class="cluster">
      <div class="cluster-head" @click="toggle(cluster)">
        <el-icon class="caret"><component :is="expanded === cluster.cluster_id ? 'ArrowDown' : 'ArrowRight'" /></el-icon>
        <span class="label">{{ cluster.label }}</span>
        <el-tag size="small" effect="plain">{{ cluster.size }} 条</el-tag>
        <span v-if="cluster.sentiment" :class="['sentiment', `tag-sentiment-${cluster.sentiment}`]">
          {{ SENTIMENT_LABELS[cluster.sentiment] }}
        </span>
        <span v-if="cluster.intent" class="intent">诉求：{{ cluster.intent }}</span>
      </div>

      <p v-if="cluster.summary" class="summary">{{ cluster.summary }}</p>

      <div class="reps">
        <div v-for="rep in cluster.representatives" :key="rep.content_uid" class="rep" @click="emit('open', rep.content_uid)">
          <el-tag size="small" effect="plain">{{ rep.platform_label }}</el-tag>
          <span class="rep-text">{{ rep.text }}</span>
          <RiskTag :level="rep.risk_level" />
          <span class="rep-meta">{{ rep.author.nickname }} · {{ formatRelative(rep.published_at) }}
            · 互动 {{ formatCount(rep.engagement.total) }}</span>
        </div>
      </div>

      <div v-if="expanded === cluster.cluster_id" v-loading="loading === cluster.cluster_id" class="members">
        <div class="members-head">
          簇内成员（共 {{ memberTotals[cluster.cluster_id] ?? cluster.size }} 条，展示前 10 条）
        </div>
        <div v-for="item in members[cluster.cluster_id] ?? []" :key="item.content_uid" class="member" @click="emit('open', item.content_uid)">
          <span :class="['sentiment', `tag-sentiment-${item.sentiment}`]">{{ SENTIMENT_LABELS[item.sentiment] }}</span>
          <span class="member-text">{{ item.text }}</span>
          <span class="rep-meta">{{ formatRelative(item.published_at) }}</span>
        </div>
      </div>
    </div>

    <p class="hint">
      簇内情感倾向为后端聚类时判定的主导情感；点击簇展开成员，点击任一条看原文与判定依据。
    </p>
  </div>
</template>

<style scoped>
.cluster-panel {
  max-height: 460px;
  overflow-y: auto;
  padding-right: 4px;
}

.cluster {
  padding: 10px 0;
  border-bottom: 1px solid var(--color-border);
}

.cluster:last-of-type {
  border-bottom: none;
}

.cluster-head {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}

.caret {
  color: var(--color-text-secondary);
}

.label {
  font-size: 14px;
  font-weight: 600;
}

.sentiment {
  font-size: 12px;
  font-weight: 600;
}

.intent {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.summary {
  margin: 6px 0;
  font-size: 13px;
  line-height: 1.7;
  color: #4b5563;
}

.reps {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.rep {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  font-size: 13px;
  border-radius: 4px;
  cursor: pointer;
}

.rep:hover {
  background: #f6f8fb;
}

.rep-text {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rep-meta {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.members {
  margin-top: 8px;
  padding: 8px 10px;
  background: #f8fafc;
  border-radius: 6px;
}

.members-head {
  margin-bottom: 6px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.member {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 0;
  font-size: 13px;
  cursor: pointer;
}

.member:hover {
  color: var(--el-color-primary);
}

.member-text {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hint {
  margin: 10px 0 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--color-text-secondary);
}
</style>
