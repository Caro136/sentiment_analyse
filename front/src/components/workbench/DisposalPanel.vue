<script setup lang="ts">
/**
 * 处置协同与应对区（FR-49~51，PRD 7.2 的第三块）。
 *
 * 三块内容：
 *   1. **工单状态流转**：可选项由后端 `allowed_transitions` 给出，前端不维护状态机副本；
 *   2. **处置轨迹**：操作人、动作、前后状态、批注、时间（FR-50 的「形成处置轨迹」）；
 *   3. **回应草案**：生成（不落库）→ 人工编辑 → 采纳（写进工单并记轨迹）。
 *
 * 五条取舍：
 *
 * - **管理员不显示本区域**：PRD 第 2 章把他排除在处置之外，后端也没有相应权限。
 *   显示成只读更好？不——看不到才是真实的能力边界，给个只读框只会让人以为自己能用。
 * - **关闭动作按角色隐藏**（`ticket:close` 仅公关）：前端隐藏只是体验，后端还会拦一次。
 * - **生成与采纳分成两步**：生成是探索（可反复试），采纳是决策（进审计）。
 *   界面上也分开：生成后出现可编辑的草案区，必须点「采纳」才落库。
 * - **转入「跟踪中」即认领，并把承办人落库**：后端把这一步记成 `claim` 轨迹，
 *   但只有传了 `assignee_id` 才写承办人。不传就会出现「轨迹写着认领、承办人是空」，
 *   所以这里顺手把当前用户作为承办人一并提交。
 * - **轨迹只增不改**：因此不提供任何编辑/删除轨迹的入口；已采纳的草案可以再编辑再采纳，
 *   旧版本留在轨迹里（不是覆盖，是追加一版）。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { eventApi, ticketApi } from '@/api/modules'
import { ApiError } from '@/api/client'
import RiskTag from '@/components/RiskTag.vue'
import type { OpinionCluster, ResponseDraft, TicketDetail, TicketStatus } from '@/api/types'
import { TICKET_STATUS_LABELS } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime, formatPercent } from '@/utils/format'

const props = defineProps<{
  eventId: string
  /** 已有聚类时直接传给草案生成，让草案聚焦争议焦点 */
  clusters: OpinionCluster[]
  /** 从告警的「处置」入口进来时带上（FR-38 → FR-49 的链路）：
   *  开单会记住来源告警，「这条舆情为什么被处理」在工单上可追溯 */
  alertId?: string
}>()

const auth = useAuthStore()

const ticket = ref<TicketDetail | null>(null)
const loading = ref(false)
const submitting = ref(false)
const drafting = ref(false)
const draft = ref<ResponseDraft | null>(null)
/** 可编辑的草案文本：生成后填入，采纳前用户可改 */
const draftText = ref('')
/** 展开查看工单上**已采纳**的版本（它是最终对外口径的底稿，必须能再看一眼） */
const showAdopted = ref(false)

const canDispose = computed(() => auth.hasRole('analyst', 'pr'))
const canClose = computed(() => auth.hasRole('pr'))

/**
 * 承办人显示名。是自己时显示「本人」而不是一串 user_id：
 * 回查 `sys_user` 拿显示名要好管理员权限（FR-03），为一个标签开口子不值当。
 */
const assigneeLabel = computed(() => {
  const id = ticket.value?.assignee_id
  if (!id) return ''
  return id === auth.user?.user_id ? '本人' : id
})

/** 流转目标：后端给的可选项，前端不再过滤一遍状态机（只按角色过滤关闭动作） */
const transitionOptions = computed<TicketStatus[]>(
  () => (ticket.value?.allowed_transitions ?? []).filter((status) => {
    const closing = status === 'resolved' || status === 'ignored'
    return !closing || canClose.value
  }),
)

const nextStatus = ref<TicketStatus | ''>('')
const note = ref('')

// ---------------------------------------------------------------------------
// 加载与操作
// ---------------------------------------------------------------------------

async function loadTicket(): Promise<void> {
  if (!props.eventId) return
  loading.value = true
  try {
    const page = await ticketApi.list({ event_id: props.eventId, page_size: 1 })
    // 列表按创建时间倒序，因此 items[0] 就是该事件最新的一张工单
    // （同一事件同时只允许一张未关闭工单，所以它就是当前要处置的那张）
    ticket.value = page.items.length ? await ticketApi.detail(page.items[0].ticket_id) : null
    draft.value = null
    draftText.value = ''
    showAdopted.value = false
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    loading.value = false
  }
}

async function createTicket(): Promise<void> {
  submitting.value = true
  try {
    ticket.value = await ticketApi.create({
      event_id: props.eventId,
      // 有来源告警就带上：工单靠它回答「这次处置是被哪条预警触发的」
      alert_id: props.alertId || undefined,
      note: note.value.trim() || (props.alertId ? '由预警告警进入处置' : '从研判工作台开单'),
    })
    note.value = ''
    ElMessage.success('工单已创建')
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    submitting.value = false
  }
}

async function submitFlow(): Promise<void> {
  const current = ticket.value
  if (!current) return

  // 关闭动作二次确认：它是不可逆的一步（终态，重新处置要新建工单）
  if (nextStatus.value === 'resolved' || nextStatus.value === 'ignored') {
    try {
      await ElMessageBox.confirm(
        `确认把工单${TICKET_STATUS_LABELS[nextStatus.value]}？关闭后不能再流转，需重新处置请新建工单。`,
        '提示', { type: 'warning' })
    } catch {
      return
    }
  }

  submitting.value = true
  try {
    // 转入「跟踪中」且此前无人承办 = 认领（后端据此记 `claim` 轨迹）。
    // 承办人一并落库：只记轨迹不写承办人，工单上会出现「轨迹写着认领、承办人却是空」，
    // 而处置最忌责任人不明（US-12 的责任分工）。
    const claiming = nextStatus.value === 'tracking' && !current.assignee_id
    ticket.value = await ticketApi.update(current.ticket_id, {
      status: (nextStatus.value || undefined) as TicketStatus | undefined,
      assignee_id: claiming ? auth.user?.user_id : undefined,
      note: note.value.trim() || undefined,
    })
    nextStatus.value = ''
    note.value = ''
    ElMessage.success(claiming ? '已认领并转入跟踪中' : '已更新处置状态')
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    submitting.value = false
  }
}

/** 只传 note：追加批注而不改状态（后端按「无 status 且只有 note」识别为批注） */
async function submitComment(): Promise<void> {
  const current = ticket.value
  if (!current) return
  if (!note.value.trim()) {
    ElMessage.warning('批注内容不能为空')
    return
  }
  submitting.value = true
  try {
    ticket.value = await ticketApi.update(current.ticket_id, { note: note.value.trim() })
    note.value = ''
    ElMessage.success('已记录批注')
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    submitting.value = false
  }
}

async function generateDraft(): Promise<void> {
  drafting.value = true
  try {
    draft.value = await eventApi.responseDraft(props.eventId, {
      // 把聚类标签带上，让草案聚焦争议焦点（材料不足时后端会拒绝生成并说明缺什么）
      cluster_labels: props.clusters.map((cluster) => cluster.label).slice(0, 10),
    })
    draftText.value = draft.value.rendered
    ElMessage.success('草案已生成（未落库，编辑后点采纳才会写入工单）')
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    drafting.value = false
  }
}

async function adoptDraft(): Promise<void> {
  const current = ticket.value
  if (!current) {
    ElMessage.warning('请先创建工单，草案采纳后会写入工单')
    return
  }
  if (draftText.value.trim().length < 10) {
    ElMessage.warning('草案内容过短，请检查后再采纳')
    return
  }
  submitting.value = true
  try {
    ticket.value = await ticketApi.adoptDraft(current.ticket_id, {
      content: draftText.value.trim(),
      note: current.has_response_draft ? '工作台编辑后再次采纳' : '工作台编辑后采纳',
    })
    // 采纳后直接把工单上的版本摊开给用户看一眼：这是对外口径的底稿，
    // 点完按钮就收起、只剩一句 toast 的话，用户会不确定自己到底存了什么
    showAdopted.value = true
    ElMessage.success('草案已采纳（旧版本保留在处置轨迹里）')
  } catch (error) {
    if (error instanceof ApiError && !error.isAuthError) ElMessage.error(error.message)
  } finally {
    submitting.value = false
  }
}

/** 把工单上已采纳的版本载入编辑器，继续改并再采纳一版 */
function loadAdoptedToEditor(): void {
  draftText.value = ticket.value?.response_draft ?? ''
  ElMessage.info('已载入编辑器：修改后点「采纳并写入工单」，旧版本会留在处置轨迹里')
}

watch(() => props.eventId, loadTicket, { immediate: true })
</script>

<template>
  <el-card v-if="canDispose" v-loading="loading" shadow="never" class="disposal">
    <template #header>
      <div class="head">
        <span>处置协同与应对</span>
        <template v-if="ticket">
          <el-tag size="small" effect="dark" type="info">{{ ticket.status_label }}</el-tag>
          <RiskTag :level="ticket.risk_level" />
          <span class="meta">工单 {{ ticket.ticket_id }}</span>
          <span v-if="assigneeLabel" class="meta">承办人 {{ assigneeLabel }}</span>
          <span v-if="ticket.alert_id" class="meta">来源告警 {{ ticket.alert_id }}</span>
          <span v-if="ticket.closed_at" class="meta">关闭于 {{ formatDateTime(ticket.closed_at) }}</span>
        </template>
        <span v-else class="meta">该事件尚无工单</span>
      </div>
    </template>

    <div class="body">
      <!-- 左：状态流转与批注 -->
      <div class="flow">
        <div v-if="!ticket" class="empty-flow">
          <p class="tip">
            开单后即可流转处置状态、记录批注并沉淀处置轨迹。
            <template v-if="alertId">本次开单会关联来源告警，便于回溯「为什么处理它」。</template>
          </p>
          <el-button type="primary" :loading="submitting" @click="createTicket">创建处置工单</el-button>
        </div>

        <template v-else>
          <div v-if="ticket.is_closed" class="closed-note">
            工单已{{ ticket.status_label }}（终态）。需要继续处置请新建工单——
            处置轨迹只增不改，重开会让历史混在一起。
          </div>

          <template v-else>
            <div class="row">
              <span class="label">流转到</span>
              <el-select v-model="nextStatus" placeholder="选择目标状态" clearable class="w-160">
                <el-option
                  v-for="status in transitionOptions"
                  :key="status"
                  :label="TICKET_STATUS_LABELS[status]"
                  :value="status"
                />
              </el-select>
              <span class="label">说明</span>
              <el-input v-model="note" placeholder="处置说明（可只填批注）" class="flex-1" />
              <el-button
                type="primary"
                :disabled="!nextStatus && !note.trim()"
                :loading="submitting"
                @click="nextStatus ? submitFlow() : submitComment()"
              >
                {{ nextStatus ? '提交流转' : '记录批注' }}
              </el-button>
            </div>
            <p class="hint">
              可选状态由后端状态机给出（只能向前，终态不可回流）；
              关闭工单仅公关运营可执行{{ canClose ? '' : '（当前角色不可关闭）' }}。
              转入「跟踪中」且此前无人承办时按「认领」记入轨迹，并把当前用户记为承办人。
            </p>
          </template>

          <!-- 处置轨迹（FR-50） -->
          <div class="trace">
            <div class="trace-title">处置轨迹（{{ ticket.logs.length }} 条）</div>
            <el-timeline class="trace-timeline">
              <el-timeline-item
                v-for="(log, index) in ticket.logs"
                :key="index"
                :timestamp="formatDateTime(log.operated_at)"
                placement="top"
                size="normal"
              >
                <div class="trace-row">
                  <el-tag size="small" effect="plain">{{ log.action_label }}</el-tag>
                  <span v-if="log.to_status" class="transition">
                    {{ log.from_status_label || '—' }} → {{ log.to_status_label }}
                  </span>
                  <span class="operator">{{ log.operator_id.slice(-6) }}</span>
                </div>
                <div v-if="log.note" class="trace-note">{{ log.note }}</div>
              </el-timeline-item>
            </el-timeline>
          </div>
        </template>
      </div>

      <!-- 右：回应草案（FR-51） -->
      <div class="draft">
        <div class="draft-head">
          <span class="draft-title">公关回应草案</span>
          <el-button :loading="drafting" @click="generateDraft">
            {{ draft ? '重新生成' : (ticket?.has_response_draft ? '重新起草' : '生成草案') }}
          </el-button>
        </div>

        <p class="hint">
          生成不落库：可以先反复试，人工编辑后点「采纳」才写入工单。
          草案由模型依据事件材料生成，可能包含未经核实的事实，发布前必须人工确认；
          采纳的文本含模型给的「发布前请确认」提示，那一段是内部事项，对外发布前请删掉。
        </p>

        <!-- 工单上已采纳的版本：它是最终对外口径的底稿，要能再看、再改 -->
        <div v-if="ticket?.response_draft" class="adopted">
          <div class="adopted-head">
            <span class="adopted-title">
              已采纳版本（{{ ticket.response_draft.length }} 字）
            </span>
            <el-button size="small" text type="primary" @click="showAdopted = !showAdopted">
              {{ showAdopted ? '收起' : '查看' }}
            </el-button>
            <el-button size="small" text type="primary" @click="loadAdoptedToEditor">
              继续编辑
            </el-button>
          </div>
          <pre v-if="showAdopted" class="adopted-text">{{ ticket.response_draft }}</pre>
        </div>

        <div v-if="draft" class="evidence">
          <span>声量 {{ draft.evidence.total_volume }}</span>
          <span>负面率 {{ formatPercent(draft.evidence.negative_ratio) }}</span>
          <span>高风险原话 {{ draft.evidence.high_risk_quotes.length }} 条</span>
          <span>官方表态 {{ draft.evidence.official_statements.length }} 条</span>
          <span>聚焦 {{ draft.evidence.cluster_labels.length }} 个观点簇</span>
        </div>

        <!-- 编辑器：新生成的草案，或从已采纳版本载入的文本 -->
        <template v-if="draft || draftText">
          <el-input
            v-model="draftText"
            type="textarea"
            :rows="12"
            class="draft-editor"
            placeholder="草案内容"
          />

          <div class="draft-actions">
            <el-button type="primary" :loading="submitting" @click="adoptDraft">
              采纳并写入工单
            </el-button>
            <el-button v-if="draft" @click="draftText = draft.rendered">还原为生成稿</el-button>
            <span v-if="!ticket" class="warn">尚无工单：采纳前请先创建工单</span>
          </div>
        </template>

        <!-- 已有采纳版本时不再提示「尚未生成」——那是自相矛盾的文案 -->
        <el-empty v-else-if="!ticket?.has_response_draft" description="尚未生成草案" :image-size="60" />
      </div>
    </div>
  </el-card>

  <el-card v-else shadow="never" class="disposal">
    <template #header>处置协同与应对</template>
    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="当前角色不参与处置：工单流转与回应草案仅限分析师与公关运营（PRD 第 2 章）。"
    />
  </el-card>
</template>

<style scoped>
.disposal {
  margin-top: 16px;
}

.head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.meta {
  font-size: 12px;
  font-weight: 400;
  color: var(--color-text-secondary);
}

.body {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}

.flow {
  min-width: 0;
}

.empty-flow {
  padding: 12px 0;
}

.tip {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--color-text-secondary);
}

.closed-note {
  padding: 8px 10px;
  font-size: 13px;
  line-height: 1.7;
  background: #f4f6f9;
  border-radius: 6px;
}

.row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.label {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.flex-1 {
  flex: 1;
}

.hint {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--color-text-secondary);
}

.trace {
  margin-top: 14px;
}

.trace-title {
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
}

.trace-timeline {
  max-height: 220px;
  overflow-y: auto;
  padding-left: 2px;
}

.trace-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.transition {
  color: var(--el-color-primary);
}

.operator {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.trace-note {
  margin-top: 2px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.draft-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.draft-title {
  font-size: 13px;
  font-weight: 600;
}

.evidence {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  margin: 8px 0;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.adopted {
  margin-top: 10px;
  padding: 8px 10px;
  background: #f4f6f9;
  border-radius: 6px;
}

.adopted-head {
  display: flex;
  align-items: center;
  gap: 4px;
}

.adopted-title {
  flex: 1;
  font-size: 13px;
  font-weight: 600;
}

.adopted-text {
  max-height: 180px;
  margin: 6px 0 0;
  overflow-y: auto;
  font-family: inherit;
  font-size: 12px;
  line-height: 1.7;
  color: var(--color-text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
}

.draft-editor {
  margin-top: 4px;
}

.draft-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
}

.warn {
  font-size: 12px;
  color: var(--risk-a);
}

.w-160 {
  width: 160px;
}
</style>
