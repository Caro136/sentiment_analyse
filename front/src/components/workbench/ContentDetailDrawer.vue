<script setup lang="ts">
/**
 * 内容详情抽屉：原文全文 + 多模态证据 + 判定依据（FR-44 / FR-33）。
 *
 * 做成**抽屉而不是右侧面板里的一个标签页**（原型是标签页）：读全文需要宽度，
 * 而时间线/聚类要留在视野里对照——抽屉可以覆盖在列表上，关掉即回到原处。
 * 这是与原型的一处有意的偏差。
 *
 * 三块内容按「证据」组织，而不是按数据表组织：
 *   1. 原文（含标题、作者、互动量、原始链接）
 *   2. 多模态证据（OCR / ASR，**失败也要显示**——ADR-06 说失败不阻断入库，
 *      用户需要知道「这条为什么没有 OCR 文本」）
 *   3. 判定依据（情感置信度、烈度、风险因子明细）
 */
import { computed, ref, watch } from 'vue'

import { contentApi } from '@/api/modules'
import { ApiError } from '@/api/client'
import RiskTag from '@/components/RiskTag.vue'
import type { ContentDetail, ModalityAsset } from '@/api/types'
import { SENTIMENT_LABELS } from '@/api/types'
import { formatCount, formatDateTime, formatPercent } from '@/utils/format'

const props = defineProps<{ uid: string | null }>()
const emit = defineEmits<{ (event: 'close'): void }>()

const detail = ref<ContentDetail | null>(null)
const loading = ref(false)
const error = ref('')

const visible = computed({
  get: () => Boolean(props.uid),
  set: (value: boolean) => {
    if (!value) emit('close')
  },
})

const assets = computed<ModalityAsset[]>(() => detail.value?.modality_assets ?? [])
const successAssets = computed(() => assets.value.filter((a) => a.parse_status === 'success'))
const failedAssets = computed(() => assets.value.filter((a) => a.parse_status !== 'success'))

/** 风险因子：模型给的理由往往带自然语言，单独提出来放在最上面 */
const riskReason = computed(() => {
  const reason = detail.value?.risk_factors?.reason
  return typeof reason === 'string' ? reason : ''
})

const riskFactors = computed(() => {
  const factors = detail.value?.risk_factors
  if (!factors) return [] as Array<{ key: string; value: string }>
  return Object.entries(factors)
    .filter(([key]) => key !== 'reason')
    .map(([key, value]) => ({ key, value: typeof value === 'number' ? value.toFixed(3) : String(value) }))
})

const ASSET_LABELS: Record<string, string> = { image: '图片 OCR', video: '视频 ASR', audio: '音频 ASR' }

async function load(uid: string): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    detail.value = await contentApi.detail(uid)
  } catch (err) {
    detail.value = null
    // 未打标的内容没有详情（详情以分析结论为核心）——如实说明而不是显示空白
    error.value = err instanceof ApiError ? err.message : '详情加载失败'
  } finally {
    loading.value = false
  }
}

watch(
  () => props.uid,
  (uid) => {
    if (uid) void load(uid)
  },
  { immediate: true },
)
</script>

<template>
  <el-drawer v-model="visible" size="46%" :with-header="false" @closed="emit('close')">
    <div v-loading="loading" class="drawer">
      <el-alert v-if="error" type="warning" :closable="false" show-icon :title="error" class="mb" />

      <template v-if="detail">
        <div class="head">
          <el-tag size="small" effect="plain">{{ detail.platform_label }}</el-tag>
          <span class="title">{{ detail.title || '（无标题）' }}</span>
          <RiskTag :level="detail.risk_level" :manual="detail.is_manual_override" />
          <!-- 抽屉去掉了默认头部（标题行自带信息），因此必须自己给一个关闭入口：
               只靠点遮罩或 ESC 关闭，用户会以为「关不掉」（截图核对时踩过） -->
          <el-button text :icon="'Close'" title="关闭（Esc）" @click="emit('close')" />
        </div>

        <div class="meta">
          <span>{{ detail.author.nickname }}</span>
          <span>粉丝 {{ formatCount(detail.author.follower_count) }}</span>
          <el-tag v-if="detail.author.is_kol" size="small" type="danger" effect="plain">KOL</el-tag>
          <el-tag v-if="detail.author.verified_type" size="small" effect="plain">
            {{ detail.author.verified_type }}
          </el-tag>
          <span>{{ formatDateTime(detail.published_at) }}</span>
          <span v-if="detail.source_keyword">来源词 {{ detail.source_keyword }}</span>
          <a v-if="detail.url" :href="detail.url" target="_blank" rel="noopener noreferrer" class="link">
            原始链接 ↗
          </a>
        </div>

        <div class="engagement">
          <span>点赞 {{ formatCount(detail.engagement.likes) }}</span>
          <span>评论 {{ formatCount(detail.engagement.comments) }}</span>
          <span>转发 {{ formatCount(detail.engagement.shares) }}</span>
          <span>合计 {{ formatCount(detail.engagement.total) }}</span>
        </div>

        <!-- 1. 原文 -->
        <el-card shadow="never" class="block">
          <template #header>原文全文</template>
          <p class="full-text">{{ detail.raw_text || detail.text || '（无正文）' }}</p>
          <p v-if="detail.topic_name" class="topic-note">归属主题：{{ detail.topic_name }}</p>
        </el-card>

        <!-- 2. 多模态证据（失败也展示，说明为什么没有文本） -->
        <el-card shadow="never" class="block">
          <template #header>
            多模态证据
            <span class="hint">成功 {{ successAssets.length }} · 失败 {{ failedAssets.length }}</span>
          </template>
          <el-empty v-if="!assets.length" description="该内容没有图片 / 音视频" :image-size="60" />
          <div v-for="asset in assets" :key="asset.asset_id" class="asset">
            <div class="asset-head">
              <el-tag size="small" effect="plain">{{ ASSET_LABELS[asset.asset_type] ?? asset.asset_type }}</el-tag>
              <span class="parser">{{ asset.parser }}</span>
              <el-tag
                size="small"
                :type="asset.parse_status === 'success' ? 'success' : asset.parse_status === 'failed' ? 'danger' : 'info'"
                effect="plain"
              >
                {{ asset.parse_status }}
              </el-tag>
              <a v-if="asset.origin_url" :href="asset.origin_url" target="_blank" rel="noopener noreferrer" class="link">
                资源 ↗
              </a>
            </div>
            <p v-if="asset.parsed_text" class="asset-text">{{ asset.parsed_text }}</p>
            <p v-else class="asset-error">
              {{ asset.error_message || '解析未产出文本（失败不阻断入库，见 ADR-06）' }}
            </p>
          </div>
        </el-card>

        <!-- 3. 判定依据 -->
        <el-card shadow="never" class="block">
          <template #header>判定依据</template>
          <div class="factor-row">
            <span class="factor-label">情感</span>
            <span>{{ SENTIMENT_LABELS[detail.sentiment] }}<template v-if="detail.emotion"> · {{ detail.emotion }}</template></span>
            <span class="factor-label">置信度</span>
            <span>{{ formatPercent(detail.sentiment_confidence) }}</span>
            <span class="factor-label">烈度</span>
            <span>{{ detail.intensity_index === null ? '-' : detail.intensity_index.toFixed(3) }}</span>
          </div>
          <p v-if="riskReason" class="risk-reason">{{ riskReason }}</p>
          <div v-if="riskFactors.length" class="factors">
            <div v-for="factor in riskFactors" :key="factor.key" class="factor-chip">
              <span class="factor-key">{{ factor.key }}</span>
              <span class="factor-value">{{ factor.value }}</span>
            </div>
          </div>
          <p v-if="detail.override_reason" class="override-note">
            人工复核理由：{{ detail.override_reason }}
          </p>
          <p class="audit-note">
            标签模型 {{ detail.model_version || '-' }} · 打标于 {{ formatDateTime(detail.analyzed_at) }}
          </p>
        </el-card>
      </template>
    </div>
  </el-drawer>
</template>

<style scoped>
.drawer {
  padding: 0 4px;
}

.mb {
  margin-bottom: 12px;
}

.head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.title {
  flex: 1;
  font-size: 16px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.engagement {
  display: flex;
  gap: 16px;
  margin-top: 6px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.link {
  color: var(--el-color-primary);
  text-decoration: none;
}

.link:hover {
  text-decoration: underline;
}

.block {
  margin-top: 12px;
}

.hint {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
  color: var(--color-text-secondary);
}

.full-text {
  margin: 0;
  font-size: 14px;
  line-height: 1.8;
  white-space: pre-wrap;
}

.topic-note {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.asset {
  padding: 8px 0;
  border-bottom: 1px dashed var(--color-border);
}

.asset:last-child {
  border-bottom: none;
}

.asset-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.parser {
  font-family: Consolas, Monaco, monospace;
}

.asset-text {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
}

.asset-error {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.factor-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  font-size: 13px;
}

.factor-label {
  color: var(--color-text-secondary);
}

.risk-reason {
  margin: 10px 0 0;
  padding: 8px 10px;
  font-size: 13px;
  line-height: 1.7;
  background: #f7f8fa;
  border-left: 3px solid var(--color-border);
  border-radius: 3px;
}

.factors {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}

.factor-chip {
  display: flex;
  gap: 4px;
  padding: 2px 8px;
  font-size: 12px;
  background: #f2f4f7;
  border-radius: 10px;
}

.factor-key {
  color: var(--color-text-secondary);
}

.override-note {
  margin: 10px 0 0;
  font-size: 13px;
  color: var(--risk-a);
}

.audit-note {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--color-text-secondary);
}
</style>
