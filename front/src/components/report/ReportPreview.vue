<script setup lang="ts">
/**
 * 报告在线预览（FR-60）。把 Markdown 正文渲染成可读的排版。
 *
 * 两条实现约定：
 *
 * 1. **解析成块结构后用模板渲染**（见 `utils/markdown.ts`）：报告正文引用了
 *    用户原文，走 `v-html` 等于把外部文本当代码执行；这里插值自动转义，
 *    注入面为零，代价是只支持 Markdown 子集——而报告结构固定，子集够用。
 * 2. **排版按「读完能看懂」来定**：正文最大宽度 860px（中文一行 30~40 字最舒服）、
 *    章节标题带分隔线、表格给边框与表头底色。这一块也是**打印预览**的样子，
 *    但正式导出 PDF 走服务端渲染的自包含 HTML（D-07，样式面向 A4 优化）。
 */
import { computed } from 'vue'

import MarkdownInline from './MarkdownInline.vue'
import { parseMarkdown } from '@/utils/markdown'

const props = defineProps<{
  markdown: string
  /** 报告标题（存在库里的 `report.title`，正文里不一定重复写） */
  title?: string
  /** 顶部元信息：研判主题、生成时间等 */
  meta?: { label: string; value: string }[]
}>()

const blocks = computed(() => parseMarkdown(props.markdown))
const metaItems = computed(() => (props.meta ?? []).filter((item) => item.value))

/**
 * 正文自带一级标题时不再重复显示库里存的标题。
 *
 * 实测模型输出的第一行常常就是 `# 某某舆情研判报告`——库里的 `title` 与它是同一句话，
 * 两个都渲染会在报告顶部出现两个几乎一样的标题（看起来像渲染 bug）。
 */
const hasOwnTitle = computed(() => {
  const first = blocks.value[0]
  return first?.kind === 'heading' && first.level === 1
})
const showLibraryTitle = computed(() => Boolean(props.title) && !hasOwnTitle.value)

/** 标题层级直接映射到 h1~h6，不再压缩：压缩会让「一、」与「（一）」看起来同级 */
function headingTag(level: number): string {
  return `h${Math.min(Math.max(level, 1), 6)}`
}

function headingClass(level: number): string {
  if (level <= 1) return 'h1'
  return level === 2 ? 'h2' : 'h3'
}
</script>

<template>
  <article class="preview">
    <header v-if="showLibraryTitle || metaItems.length" class="preview-title">
      <h1 v-if="showLibraryTitle">{{ title }}</h1>
      <div v-if="metaItems.length" class="preview-meta">
        <span v-for="item in metaItems" :key="item.label">{{ item.label }}：{{ item.value }}</span>
      </div>
    </header>

    <template v-for="(block, index) in blocks" :key="index">
      <component
        :is="headingTag(block.level)"
        v-if="block.kind === 'heading'"
        :class="headingClass(block.level)"
      >
        <MarkdownInline :segments="block.segments" />
      </component>

      <p v-else-if="block.kind === 'paragraph'" class="paragraph">
        <MarkdownInline :segments="block.segments" />
      </p>

      <ul v-else-if="block.kind === 'list' && !block.ordered" class="list">
        <li v-for="(item, itemIndex) in block.items" :key="itemIndex">
          <MarkdownInline :segments="item" />
        </li>
      </ul>

      <ol v-else-if="block.kind === 'list'" class="list" :start="block.start">
        <li v-for="(item, itemIndex) in block.items" :key="itemIndex">
          <MarkdownInline :segments="item" />
        </li>
      </ol>

      <blockquote v-else-if="block.kind === 'quote'" class="quote">
        <MarkdownInline :segments="block.segments" />
      </blockquote>

      <div v-else-if="block.kind === 'table'" class="table-wrap">
        <table>
          <thead>
            <tr>
              <th
                v-for="(cell, cellIndex) in block.header"
                :key="cellIndex"
                :style="{ textAlign: block.aligns[cellIndex] ?? 'left' }"
              >
                <MarkdownInline :segments="cell" />
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, rowIndex) in block.rows" :key="rowIndex">
              <td
                v-for="(cell, cellIndex) in row"
                :key="cellIndex"
                :style="{ textAlign: block.aligns[cellIndex] ?? 'left' }"
              >
                <MarkdownInline :segments="cell" />
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <pre v-else-if="block.kind === 'code'" class="code">{{ block.text }}</pre>

      <hr v-else-if="block.kind === 'divider'" class="divider" />
    </template>
  </article>
</template>

<style scoped>
.preview {
  max-width: 860px;
  font-size: 14px;
  line-height: 1.9;
  color: #1f2937;
  word-break: break-word;
}

.preview-title {
  padding-bottom: 12px;
  border-bottom: 2px solid var(--color-border);
}

.preview-title h1 {
  margin: 0 0 8px;
  font-size: 22px;
  line-height: 1.5;
}

.preview-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 18px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

/* 正文自带的一级标题（模型输出的首行）与库里的标题同义，样式上也按一级标题来 */
.h1 {
  margin: 0 0 14px;
  font-size: 21px;
  line-height: 1.5;
}

.h2 {
  margin: 26px 0 10px;
  padding-left: 10px;
  font-size: 17px;
  line-height: 1.5;
  border-left: 3px solid var(--el-color-primary);
}

.h3 {
  margin: 20px 0 8px;
  font-size: 15px;
}

.paragraph {
  margin: 10px 0;
}

.list {
  margin: 10px 0;
  padding-left: 22px;
}

.list li {
  margin: 4px 0;
}

.quote {
  margin: 12px 0;
  padding: 6px 14px;
  color: #475569;
  background: #f6f8fa;
  border-left: 3px solid #cbd5e1;
}

.table-wrap {
  margin: 12px 0;
  overflow-x: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

th,
td {
  padding: 7px 10px;
  border: 1px solid var(--color-border);
}

th {
  font-weight: 600;
  background: #f6f8fa;
}

.code {
  margin: 12px 0;
  padding: 10px 12px;
  overflow-x: auto;
  font-size: 12.5px;
  line-height: 1.7;
  background: #f6f8fa;
  border-radius: 6px;
}

.divider {
  margin: 20px 0;
  border: none;
  border-top: 1px solid var(--color-border);
}
</style>
