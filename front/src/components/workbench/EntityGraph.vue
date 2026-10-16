<script setup lang="ts">
/**
 * 实体关系图谱（FR-46，PRD 3.4 的 `EntityGraph`）。
 *
 * 力导向图用 ECharts 的 `graph` 系列。三个实现要点：
 *
 * 1. **节点按实体类型着色、按关系权重定边宽**：实体类型是理解图谱的主线索
 *    （机构/人物/产品/事件/地点），关系权重让「谁和谁关系最强」一眼可见。
 * 2. **点击节点高亮其邻接边**（`emphasis.focus: 'adjacency'`）：图谱一多就看不清，
 *    聚焦邻接是最基本的可读性手段。
 * 3. **后端已限制关系条数**（默认取权重最高的 60 条），返回 `truncated` 时在界面上
 *    说明——否则用户会以为这张图就是全部关系。
 */
import { computed, ref, watch } from 'vue'
import type { EChartsOption } from 'echarts'

import type { EntityGraphView, GraphNode } from '@/api/types'
import { useChart } from '@/utils/chart'

const props = defineProps<{ graph: EntityGraphView | null }>()
const emit = defineEmits<{ (event: 'focus', name: string): void }>()

const chartRef = ref<HTMLElement>()
const selected = ref<GraphNode | null>(null)
const chart = useChart(chartRef, {
  onClick: (params) => {
    const node = props.graph?.nodes.find((item) => item.name === params.name) ?? null
    selected.value = node
  },
})

/** 实体类型 → 颜色。类型是后端自由文本（机构/人物/产品/事件/地点） */
const TYPE_COLORS: Record<string, string> = {
  机构: '#2f6fed',
  人物: '#e8663d',
  产品: '#52a55c',
  事件: '#c81e1e',
  地点: '#8a6fd1',
}

const categories = computed(() => {
  const types = Array.from(new Set((props.graph?.nodes ?? []).map((n) => n.type)))
  return types.map((name) => ({ name, itemStyle: { color: TYPE_COLORS[name] ?? '#7c8794' } }))
})

const relationCount = computed(() => props.graph?.edges.length ?? 0)

function render(): void {
  const graph = props.graph
  if (!graph?.nodes.length) return

  const maxWeight = Math.max(...graph.edges.map((e) => e.weight), 1)
  const option: EChartsOption = {
    tooltip: {
      formatter: (params: unknown) => {
        const item = params as { dataType?: string; data?: { name?: string; type?: string; value?: number } }
        if (item.dataType === 'edge') {
          return `${item.data?.name ?? ''}<br/>权重 ${item.data?.value ?? 0}`
        }
        return `${item.data?.name ?? ''}<br/>类型 ${item.data?.type ?? ''}`
      },
    },
    legend: { data: categories.value.map((c) => c.name), bottom: 0 },
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        categories: categories.value,
        // 力导向参数：节点数不多（几十个）时这个组合能较快收敛且不太挤
        force: { repulsion: 260, edgeLength: [60, 140], gravity: 0.08 },
        label: { show: true, fontSize: 11, position: 'right' },
        emphasis: { focus: 'adjacency', label: { fontSize: 13, fontWeight: 'bold' } },
        // 节点大小按连接度（出现次数）区分：中心实体更大
        data: graph.nodes.map((node) => {
          const degree = graph.edges.filter((e) => e.source === node.id || e.target === node.id).length
          return {
            id: node.id,
            name: node.name,
            type: node.type,
            value: degree,
            symbolSize: 14 + Math.min(degree, 8) * 3,
            category: node.type,
          }
        }),
        links: graph.edges.map((edge) => ({
          source: edge.source,
          target: edge.target,
          name: edge.type,
          value: edge.weight,
          lineStyle: { width: 1 + (edge.weight / maxWeight) * 3, opacity: 0.6, curveness: 0.08 },
        })),
      },
    ],
  }
  chart.render(option)
}

watch(() => props.graph, render, { deep: true })
</script>

<template>
  <div class="graph-panel">
    <div class="panel-head">
      <span class="stat">
        {{ graph?.nodes.length ?? 0 }} 个实体 · {{ relationCount }} 条关系
        <el-tag v-if="graph?.truncated" size="small" type="warning" effect="plain">
          仅显示权重最高的若干条关系
        </el-tag>
      </span>
      <el-button
        v-if="selected"
        size="small"
        text
        type="primary"
        @click="emit('focus', selected.name)"
      >
        用「{{ selected.name }}」去筛选内容
      </el-button>
    </div>

    <div ref="chartRef" class="chart"></div>
    <el-empty v-if="!graph?.nodes.length" description="该事件暂无实体关系" :image-size="70" />
    <p class="hint">
      节点大小＝关联实体数，边宽＝共现权重；点击实体可高亮其邻接关系并可跳去筛选内容。
      实体与关系由后端从事件内容中抽取（T-11）。
    </p>
  </div>
</template>

<style scoped>
.graph-panel {
  max-height: 480px;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.stat {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.chart {
  width: 100%;
  height: 400px;
}

.hint {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--color-text-secondary);
}
</style>
