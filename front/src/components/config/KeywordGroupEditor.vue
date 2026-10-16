<script setup lang="ts">
/**
 * 一个词包分组的编辑器（品牌词 / 事件词 / 竞品词 / 排除词）。
 *
 * 三条取舍：
 *
 * 1. **用表格而不是标签云**：权重是词条的一部分（0.1~9.99，用于命中排序），
 *    标签云里塞不进一个数字输入框；表格能同时看清「有哪些词」和「各是什么权重」。
 *    实测演示数据里就有 0.7 / 0.9 / 0.6 这些非默认权重。
 * 2. **权重必须原样提交**：词包接口是**全量替换**，删掉 weight 字段不会「保持不变」，
 *    而是把整组词的权重悄悄重置为 1.0。因此这里把权重当成一等字段来编辑。
 * 3. **同组重复词由后端去重**（保留较大权重），前端不拦——前端拦一遍会让
 *    「为什么没加进去」变成猜谜；提交后以返回的词包为准（`emit` 出去的列表会被父级替换）。
 */
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'

import type { KeywordGroup, KeywordItem } from '@/api/types'

const props = defineProps<{
  group: KeywordGroup
  label: string
  hint: string
  /** 父级持有的完整词包（四个分组共享一份，因此这里只取自己那一段） */
  items: KeywordItem[]
  disabled?: boolean
}>()

const emit = defineEmits<{ 'update:items': [KeywordItem[]] }>()

const newWord = ref('')
const newWeight = ref(1)

/** 本组的词条。顺序即录入顺序，提交时保持稳定 */
const rows = computed(() => props.items.filter((item) => item.group_type === props.group))

/** 把本组的改动写回完整词包：换掉本组那一段，其余分组原样保留 */
function emitRows(next: KeywordItem[]): void {
  emit('update:items', [
    ...props.items.filter((item) => item.group_type !== props.group),
    ...next,
  ])
}

function addWord(): void {
  const word = newWord.value.trim()
  if (!word) return
  if (rows.value.some((item) => item.word === word)) {
    ElMessage.warning(`「${word}」已在${props.label}中`)
    return
  }
  emitRows([...rows.value, { word, group_type: props.group, weight: newWeight.value }])
  newWord.value = ''
}

function removeWord(word: string): void {
  emitRows(rows.value.filter((item) => item.word !== word))
}

function setWeight(word: string, weight: number | undefined): void {
  // 空输入（用户清空后还没填）不动权重，避免把 0 写进去
  if (typeof weight !== 'number' || Number.isNaN(weight)) return
  emitRows(rows.value.map((item) => (
    item.word === word ? { ...item, weight } : item
  )))
}
</script>

<template>
  <div class="group">
    <div class="group-head">
      <span class="group-label">{{ label }}</span>
      <span class="group-count">{{ rows.length }} 个词</span>
      <span class="group-hint">{{ hint }}</span>
    </div>

    <div v-if="rows.length" class="rows">
      <div v-for="item in rows" :key="item.word" class="row">
        <span class="word">{{ item.word }}</span>
        <el-input-number
          :model-value="item.weight"
          :min="0.1"
          :max="9.99"
          :step="0.1"
          :precision="2"
          size="small"
          controls-position="right"
          class="weight"
          :disabled="disabled"
          @change="(value: number | undefined) => setWeight(item.word, value)"
        />
        <el-button size="small" text type="danger" :disabled="disabled" @click="removeWord(item.word)">
          删除
        </el-button>
      </div>
    </div>
    <p v-else class="empty">该组暂无词条</p>

    <div class="add-row">
      <el-input
        v-model="newWord"
        size="small"
        placeholder="输入词语后回车添加"
        :disabled="disabled"
        @keyup.enter="addWord"
      />
      <el-input-number
        v-model="newWeight"
        :min="0.1"
        :max="9.99"
        :step="0.1"
        :precision="2"
        size="small"
        controls-position="right"
        class="weight"
        :disabled="disabled"
      />
      <el-button size="small" :disabled="disabled || !newWord.trim()" @click="addWord">添加</el-button>
    </div>
  </div>
</template>

<style scoped>
.group + .group {
  margin-top: 14px;
}

.group-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.group-label {
  font-size: 13px;
  font-weight: 600;
}

.group-count {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.group-hint {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.rows {
  margin-top: 6px;
}

.row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 0;
}

.word {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.weight {
  width: 110px;
}

.empty {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.add-row {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
</style>
