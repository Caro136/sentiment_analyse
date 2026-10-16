<script setup lang="ts">
/**
 * 行内片段渲染（加粗 / 斜体 / 行内代码 / 链接）。
 *
 * **一律用插值，不用 `v-html`**：片段里的文本可能来自微博、论坛的用户原文
 * （模型会引用它们），插值会自动转义，`<script>` 只会原样显示成文字。
 * 这一个组件承担了报告预览的注入安全，因此报告预览只能从这里出行内内容。
 */
import type { InlineSegment } from '@/utils/markdown'

defineProps<{ segments: InlineSegment[] }>()
</script>

<template>
  <template v-for="(segment, index) in segments" :key="index">
    <strong v-if="segment.bold">{{ segment.text }}</strong>
    <em v-else-if="segment.italic">{{ segment.text }}</em>
    <code v-else-if="segment.code" class="inline-code">{{ segment.text }}</code>
    <a v-else-if="segment.href" :href="segment.href" target="_blank" rel="noopener noreferrer">
      {{ segment.text }}
    </a>
    <template v-else>{{ segment.text }}</template>
  </template>
</template>

<style scoped>
.inline-code {
  padding: 1px 5px;
  font-family: 'SFMono-Regular', Consolas, 'Courier New', monospace;
  font-size: 0.92em;
  background: #f2f4f7;
  border-radius: 4px;
}
</style>
