/**
 * ECharts 生命周期封装。
 *
 * 三件事在每张图上都要做，散在各视图里必然会漏掉其中一件：
 *   1. 挂载后初始化、卸载前销毁（不销毁会泄漏 canvas 与事件监听）
 *   2. 容器尺寸变化时 resize（侧边栏折叠、窗口缩放都会改变容器宽度，
 *      而 ECharts 只在初始化时读一次尺寸——不 resize 的图表在缩放后是错位的）
 *   3. 数据更新时**用 setOption 而不是重建实例**（重建会丢动画、闪烁）
 *
 * 因此这里只暴露 `render(option)`，由调用方在数据变化时调用即可。
 */
import * as echarts from 'echarts'
import { onBeforeUnmount, onMounted, shallowRef, type Ref } from 'vue'

export interface ChartClickParams {
  /** 数据项名称（如扇区的「负向」、类目的时间标签） */
  name: string
  /** 数值 */
  value: number
  /** 系列名 */
  seriesName?: string
  /** 数据索引 */
  dataIndex: number
}

export interface ChartOptions {
  /**
   * 点击数据项的回调（下钻用）。
   *
   * **必须走 ECharts 的事件而不是在外层 div 上监听 DOM 点击**：图表的点击目标是
   * canvas，拿不到「点了哪个扇区」——只能靠事件里的 name/value。实测用
   * `event.target.textContent` 猜文本会在部分浏览器/缩放下完全失效。
   */
  onClick?: (params: ChartClickParams) => void
}

export interface ChartHandle {
  /** 渲染或更新图表（内部走 setOption） */
  render: (option: echarts.EChartsOption) => void
  /** 手动触发一次尺寸重算（如侧边栏折叠后） */
  resize: () => void
}

export function useChart(container: Ref<HTMLElement | undefined>,
                         options: ChartOptions = {}): ChartHandle {
  const instance = shallowRef<echarts.ECharts>()
  let observer: ResizeObserver | undefined
  /** 事件只绑一次：setOption 不会清掉已绑定的监听，重复绑会触发多次下钻 */
  let bound = false

  function resize(): void {
    instance.value?.resize()
  }

  function render(option: echarts.EChartsOption): void {
    if (!container.value) return
    if (!instance.value) {
      instance.value = echarts.init(container.value)
    }
    if (!bound && options.onClick) {
      instance.value.on('click', (params) => {
        options.onClick?.({
          name: String((params as { name?: unknown }).name ?? ''),
          value: Number((params as { value?: unknown }).value ?? 0),
          seriesName: (params as { seriesName?: string }).seriesName,
          dataIndex: Number((params as { dataIndex?: number }).dataIndex ?? -1),
        })
      })
      bound = true
    }
    // notMerge=false：保留未在本次 option 中出现的配置，避免每次更新都重置交互状态
    instance.value.setOption(option)
  }

  onMounted(() => {
    if (typeof ResizeObserver === 'undefined' || !container.value) return
    observer = new ResizeObserver(() => resize())
    observer.observe(container.value)
  })

  onBeforeUnmount(() => {
    observer?.disconnect()
    instance.value?.dispose()
    instance.value = undefined
  })

  return { render, resize }
}
