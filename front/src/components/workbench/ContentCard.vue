<script setup lang="ts">
/**
 * 舆情卡片（PRD 3.4 的卡片流单元）。
 *
 * 四个字段是「一眼判断要不要点进去」的关键：平台、时间、互动量、情感与风险。
 * 因此它们固定在一行，正文只占两行——卡片流的信息密度比正文长度更重要。
 *
 * 关键词高亮：从大盘下钻带来的 `keyword` 会在摘要里标出来，
 * 让用户立刻看到「为什么这条被筛出来了」。
 */
import { computed } from 'vue'

import RiskTag from '@/components/RiskTag.vue'
import { SENTIMENT_LABELS, type ContentItem } from '@/api/types'
import { formatCount, formatPercent, formatRelative } from '@/utils/format'

const props = withDefaults(
  defineProps<{
    item: ContentItem
    /** 需要高亮的关键词（来自筛选条件） */
    highlight?: string
    active?: boolean
  }>(),
  { highlight: '', active: false },
)

const emit = defineEmits<{ (event: 'open', uid: string): void }>()

const sentimentClass = computed(() => `tag-sentiment-${props.item.sentiment}`)

/** 把命中的关键词包成 <mark>；先转义再替换，避免正文里的 HTML 被当标签渲染 */
const highlightedText = computed(() => {
  const text = props.item.text || props.item.title || '（无正文）'
  const escaped = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  const keyword = props.highlight.trim()
  if (!keyword) return escaped
  const pattern = new RegExp(keyword.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi')
  return escaped.replace(pattern, (match) => `<mark>${match}</mark>`)
})
</script>

<template>
  <div :class="['content-card', { active }]" @click="emit('open', item.content_uid)">
    <div class="card-head">
      <el-tag size="small" effect="plain">{{ item.platform_label }}</el-tag>
      <span class="time">{{ formatRelative(item.published_at) }}</span>
      <span class="engagement" :title="`点赞 ${item.engagement.likes} · 评论 ${item.engagement.comments} · 转发 ${item.engagement.shares}`">
        互动 {{ formatCount(item.engagement.total) }}
      </span>
      <el-tag v-if="item.is_suspected_bot" size="small" type="warning" effect="plain">疑似水军</el-tag>
      <el-tag v-if="item.record_type === 'comment'" size="small" type="info" effect="plain">评论</el-tag>
    </div>

    <div class="card-text" v-html="highlightedText"></div>

    <div class="card-foot">
      <span :class="['sentiment', sentimentClass]">
        {{ SENTIMENT_LABELS[item.sentiment] }}<template v-if="item.emotion">·{{ item.emotion }}</template>
      </span>
      <span class="confidence" v-if="item.sentiment_confidence !== null">
        置信 {{ formatPercent(item.sentiment_confidence, 0) }}
      </span>
      <RiskTag :level="item.risk_level" class="risk" />
      <span v-if="item.intensity_index !== null" class="intensity">
        烈度 {{ item.intensity_index.toFixed(2) }}
      </span>
      <span class="author">{{ item.author.nickname }}</span>
    </div>
  </div>
</template>

<style scoped>
.content-card {
  padding: 10px 12px;
  margin-bottom: 8px;
  background: #fff;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.content-card:hover {
  border-color: var(--el-color-primary-light-5);
  box-shadow: 0 2px 8px rgb(0 0 0 / 6%);
}

/* 选中态：时间线/聚类/图谱互跳时会高亮对应卡片 */
.content-card.active {
  border-color: var(--el-color-primary);
  box-shadow: 0 0 0 2px var(--el-color-primary-light-8);
}

.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.engagement {
  margin-left: auto;
}

.card-text {
  margin: 6px 0;
  font-size: 13px;
  line-height: 1.65;
  /* 卡片只展示两行：信息密度靠数量而不是单条长度 */
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.card-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.sentiment {
  font-weight: 600;
}

.risk {
  margin-left: 2px;
}

.author {
  margin-left: auto;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:deep(mark) {
  padding: 0 2px;
  color: inherit;
  background: #ffe58f;
  border-radius: 2px;
}
</style>
