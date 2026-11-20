<template>
  <section class="page" data-module="availability">
    <header class="page-head">
      <div>
        <h2>可用观测</h2>
        <p class="page-desc">
          按营业日历与采集证据计算有凭据支撑的营业分钟：缺报不折算成零销量，停采分类记账，单位时间指标只用实采分钟作分母。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="reload">重算指标</button>
        <button class="btn" type="button" @click="verifyScenarios">核对示例场景</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
        <span class="stat-note">{{ item.note }}</span>
      </article>
    </div>

    <section class="panel">
      <header class="panel-head">
        <h3>覆盖分解</h3>
        <span class="panel-desc">{{ metric?.definition }}（{{ metric?.rate_unit }}）</span>
      </header>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="item in metric?.decomposition ?? []" :key="item.label">{{ item.label }}</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td v-for="item in metric?.decomposition ?? []" :key="item.label">{{ item.minutes }} 分钟</td>
          </tr>
        </tbody>
      </table>
      <p class="panel-foot">{{ metric?.denominator_rule }}</p>
    </section>

    <table class="data-table">
      <thead>
        <tr>
          <th>窗口编号</th>
          <th>数据源</th>
          <th>场景</th>
          <th>状态</th>
          <th>有效分母</th>
          <th>实采分钟</th>
          <th>缺报分钟</th>
          <th>计划停采</th>
          <th>意外停采</th>
          <th>覆盖率</th>
          <th>发布读数</th>
          <th>基线可比</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in windows" :key="row.window_id" :class="{ selected: row.window_id === selectedId }">
          <td>{{ row.window_id }}</td>
          <td>{{ row.source_name ?? row.source_id ?? '—' }}</td>
          <td>{{ row.scenario ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td>{{ row.effective_minutes }}</td>
          <td>{{ row.observed_minutes }}</td>
          <td>{{ row.missing_minutes }}</td>
          <td>{{ row.planned_blackout_minutes }}</td>
          <td>{{ row.unplanned_blackout_minutes }}</td>
          <td>{{ row.coverage_label }}</td>
          <td>
            <template v-if="row.published_rate !== null">
              {{ row.published_rate }} {{ row.rate_unit }}
              <small class="denominator">/ {{ row.rate_denominator_minutes }} 分钟</small>
            </template>
            <span v-else class="empty-tag">空态</span>
          </td>
          <td>
            <span :class="row.baseline?.comparable ? 'ok-tag' : 'warn-tag'">
              {{ row.baseline?.comparable ? '可比' : '不可比' }}
            </span>
          </td>
          <td class="row-actions">
            <button class="link" type="button" @click="select(row)">明细</button>
            <button
              v-if="row.failed_batches?.length"
              class="link"
              type="button"
              @click="retry(row)"
            >
              重试取数
            </button>
          </td>
        </tr>
        <tr v-if="!windows.length">
          <td :colspan="13" class="empty-state">暂无采集窗口，先确认采集源已接入营业日历</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ windows.length }} 个采集窗口</span>
      <span v-if="message" class="error-text">{{ message }}</span>
    </footer>

    <Teleport to="body">
      <div v-if="detail" class="modal-overlay" @click.self="detail = null">
        <div class="modal-card wide">
          <header class="modal-head">
            <h3>{{ detail.window_id }} · {{ detail.status }}</h3>
            <button class="btn ghost" type="button" @click="detail = null">&times;</button>
          </header>
          <div class="modal-body">
            <p class="detail-line">{{ detail.message }}</p>

            <section v-if="detail.empty_states?.length" class="detail-block">
              <h4>空态</h4>
              <ul>
                <li v-for="item in detail.empty_states" :key="item.reason">{{ item.reason }}</li>
              </ul>
            </section>

            <section v-if="detail.gaps?.length" class="detail-block">
              <h4>缺口归集</h4>
              <ul>
                <li v-for="item in detail.gaps" :key="item.reason">
                  {{ item.reason }}：{{ item.minutes }} 分钟
                  <span v-if="item.detail" class="muted">（{{ item.detail }}）</span>
                </li>
              </ul>
            </section>

            <section class="detail-block">
              <h4>观测批次</h4>
              <table class="data-table compact">
                <thead>
                  <tr><th>批次</th><th>类型</th><th>区间</th><th>条数</th><th>计入营业分钟</th><th>到达时间</th></tr>
                </thead>
                <tbody>
                  <tr v-for="item in detail.observations" :key="item.batch_id">
                    <td>{{ item.batch_id }}</td>
                    <td>{{ item.kind }}</td>
                    <td>{{ item.start }}–{{ item.end }}</td>
                    <td>{{ item.count }}</td>
                    <td>{{ item.observed_minutes }}</td>
                    <td>{{ item.arrived_at ?? '—' }}</td>
                  </tr>
                  <tr v-if="!detail.observations?.length">
                    <td colspan="6" class="empty-state">没有可核验的观测批次</td>
                  </tr>
                </tbody>
              </table>
              <p v-if="detail.failed_batches?.length" class="muted">
                取数失败 {{ detail.failed_batches.length }} 个批次，可重试；失败期间这些分钟按缺报记账。
              </p>
            </section>

            <section v-if="detail.blackouts?.length" class="detail-block">
              <h4>停采回执</h4>
              <ul>
                <li v-for="item in detail.blackouts" :key="item.blackout_id">
                  {{ item.kind }}：{{ item.start }}–{{ item.end }}，营业内 {{ item.business_minutes }} 分钟
                  <span v-if="item.reason" class="muted">（{{ item.reason }}）</span>
                </li>
              </ul>
              <p class="muted">
                回执重叠分钟已去重 {{ detail.overlapping_receipt_minutes_deduped }} 分钟，两类停采合计 {{ detail.blackout_minutes }} 分钟。
              </p>
            </section>

            <section v-if="detail.baseline" class="detail-block">
              <h4>基线</h4>
              <p>{{ detail.baseline.reason }}</p>
              <p v-if="detail.baseline.available" class="muted">
                基线 {{ detail.baseline.baseline_rate }} {{ detail.rate_unit }}（{{ detail.baseline.baseline_window }}，{{ detail.baseline.baseline_minutes }} 分钟）
                <span v-if="detail.baseline.delta !== null">，当期差 {{ detail.baseline.delta }}</span>
              </p>
            </section>
          </div>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="verifyText" class="modal-overlay" @click.self="verifyText = ''">
        <div class="modal-card wide">
          <header class="modal-head">
            <h3>示例场景核对</h3>
            <button class="btn ghost" type="button" @click="verifyText = ''">&times;</button>
          </header>
          <div class="modal-body">
            <p class="detail-line">{{ verifyText }}</p>
          </div>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchJson } from '@/api/client'

type Gap = { reason: string; minutes: number; detail?: string }
type WindowRow = {
  window_id: string
  source_id?: string
  source_name?: string
  scenario?: string
  status: string
  message: string
  business_minutes: number
  effective_minutes: number
  observed_minutes: number
  missing_minutes: number
  planned_blackout_minutes: number
  unplanned_blackout_minutes: number
  blackout_minutes: number
  overlapping_receipt_minutes_deduped: number
  observation_count: number
  coverage: number | null
  coverage_label: string
  published_rate: number | null
  rate_unit: string
  rate_denominator_minutes: number
  gaps: Gap[]
  observations: { batch_id: string; kind: string; start: number; end: number; count: number; observed_minutes: number; arrived_at?: string }[]
  failed_batches: { batch_id: string }[]
  blackouts: { blackout_id: string; kind: string; start: number; end: number; business_minutes: number; reason?: string }[]
  baseline: { available: boolean; comparable: boolean; baseline_rate: number | null; baseline_window?: string; baseline_minutes?: number; delta: number | null; reason: string } | null
  empty_states: { scope: string; reason: string }[]
}
type MetricEntry = {
  metric_name: string
  definition: string
  denominator_rule: string
  rate_unit: string
  decomposition: { label: string; minutes: number }[]
  totals: { observed_minutes: number; missing_minutes: number; coverage_label: string; published_rate: number | null; empty_states: { window_id: string }[] }
  windows: WindowRow[]
}

const ENDPOINT = '/api/availability'
const metric = ref<MetricEntry | null>(null)
const windows = ref<WindowRow[]>([])
const detail = ref<WindowRow | null>(null)
const selectedId = ref('')
const message = ref('')
const verifyText = ref('')

const statCards = computed(() => {
  const totals = metric.value?.totals
  return [
    { label: '实采营业分钟', value: totals?.observed_minutes ?? 0, note: `覆盖率 ${totals?.coverage_label ?? '—'}` },
    { label: '缺报分钟', value: totals?.missing_minutes ?? 0, note: '不进分母，不折算销量' },
    {
      label: '当期读数',
      value: totals?.published_rate === null || totals?.published_rate === undefined ? '空态' : `${totals.published_rate} 笔/分钟`,
      note: '分母只取实采分钟',
    },
    { label: '空态窗口', value: totals?.empty_states?.length ?? 0, note: '缺报与零销量无法区分的窗口' },
  ]
})

function select(row: WindowRow) {
  selectedId.value = row.window_id
  detail.value = row
}

async function retry(row: WindowRow) {
  message.value = ''
  try {
    const payload = await fetchJson<{ ok: boolean; message: string }>(`${ENDPOINT}/windows/${row.window_id}/retry`, {
      method: 'POST',
    })
    message.value = payload.message
    await reload()
  } catch (error) {
    message.value = error instanceof Error ? error.message : '重试取数失败'
  }
}

async function verifyScenarios() {
  message.value = ''
  try {
    const payload = await fetchJson<{ items: { window_id: string; status: string; effective_minutes: number; observed_minutes: number; missing_minutes: number; coverage_label: string; published_rate: number | null; baseline_comparable: boolean; baseline_reason: string }[] }>(
      `${ENDPOINT}/verify`,
    )
    verifyText.value = payload.items
      .map(
        (item) =>
          `${item.window_id}：状态 ${item.status}，有效分母 ${item.effective_minutes} 分钟，实采 ${item.observed_minutes} 分钟，缺口 ${item.missing_minutes} 分钟，覆盖 ${item.coverage_label}，读数 ${
            item.published_rate === null ? '空态' : `${item.published_rate} 笔/分钟`
          }；基线${item.baseline_comparable ? '可比' : '不可比'}——${item.baseline_reason}`,
      )
      .join('\n')
  } catch (error) {
    message.value = error instanceof Error ? error.message : '核对示例场景失败'
  }
}

async function reload() {
  message.value = ''
  try {
    const payload = await fetchJson<MetricEntry>(`${ENDPOINT}/metric`)
    metric.value = payload
    windows.value = payload.windows ?? []
    if (detail.value) {
      detail.value = windows.value.find((row) => row.window_id === detail.value?.window_id) ?? null
    }
  } catch (error) {
    message.value = error instanceof Error ? error.message : '可用观测指标读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.stat-note { display: block; color: var(--muted); font-size: 11px; }
.panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 16px; }
.panel-head { display: flex; justify-content: space-between; align-items: baseline; }
.panel-head h3 { margin: 0; font-size: 14px; }
.panel-desc, .panel-foot { color: var(--muted); font-size: 12px; }
.panel-foot { margin: 8px 0 0; }
.selected { background: #eef4ff; }
.denominator { color: var(--muted); }
.empty-tag { color: #b42318; }
.ok-tag { color: #067647; }
.warn-tag { color: #b54708; }
.modal-card.wide { width: 720px; }
.detail-line { margin: 0; white-space: pre-line; }
.detail-block h4 { margin: 0 0 6px; font-size: 13px; }
.detail-block ul { margin: 0; padding-left: 18px; font-size: 13px; }
.muted { color: var(--muted); font-size: 12px; }
.data-table.compact th, .data-table.compact td { padding: 5px 8px; font-size: 12px; }
</style>
