<template>
  <section class="panel" data-testid="publish-confirm">
    <header class="panel-head">
      <div>
        <h3>发布确认</h3>
        <p class="panel-hint">
          发布行提交后在这里确认版本与同版分发清单；断线后用原发布编号重提即可续做。
        </p>
      </div>
      <button class="btn ghost" type="button" @click="reset">关闭</button>
    </header>

    <div class="panel-toolbar">
      <label class="filter-item">
        <span>发布编号</span>
        <input v-model="publishRef" data-testid="publish-ref" placeholder="填写发布行 id" />
      </label>
      <button class="btn" data-testid="load-manifest" type="button" @click="loadManifest">
        回查发布清单
      </button>
      <button class="btn primary" data-testid="submit-publish" type="button" @click="submitPublish">
        确认发布并登记清单
      </button>
      <button class="btn" data-testid="load-receipt" type="button" @click="loadReceipt">
        查完成回执
      </button>
    </div>

    <p v-if="message" :class="['panel-message', messageOk ? 'ok-text' : 'error-text']" data-testid="publish-message">
      {{ message }}
    </p>

    <div class="panel-grid">
      <article class="panel-card">
        <h4>发布版本</h4>
        <dl>
          <dt>发布编号</dt>
          <dd data-testid="publish-id">{{ manifest?.publish_id ?? (publishRef || '—') }}</dd>
          <dt>版本标识</dt>
          <dd data-testid="publish-version">{{ manifest?.version || '—' }}</dd>
          <dt>发布状态</dt>
          <dd data-testid="publish-status">{{ publishRow?.status || '—' }}</dd>
          <dt>版本确认</dt>
          <dd data-testid="publish-confirmed">{{ manifest ? (manifest.confirmed ? '已确认' : '未确认') : '—' }}</dd>
          <dt>清单状态</dt>
          <dd data-testid="manifest-status">{{ manifest ? (manifest.manifest_confirmed ? '已确认' : manifest.publish?.['清单状态'] || '待登记') : '—' }}</dd>
          <dt>金记录数</dt>
          <dd data-testid="publish-golden-count">{{ manifest?.publish?.['金记录数'] ?? '—' }}</dd>
        </dl>
      </article>

      <article class="panel-card">
        <h4>完成回执</h4>
        <template v-if="receipt">
          <dl>
            <dt>回执编号</dt>
            <dd data-testid="receipt-id">{{ receipt['回执编号'] }}</dd>
            <dt>状态</dt>
            <dd data-testid="receipt-status">{{ receipt['状态'] }}</dd>
            <dt>清单数</dt>
            <dd>{{ receipt['清单数'] }}</dd>
            <dt>完成时间</dt>
            <dd>{{ receipt['完成时间'] }}</dd>
          </dl>
        </template>
        <p v-else class="panel-hint" data-testid="receipt-empty">暂无完成回执</p>
      </article>
    </div>

    <table class="data-table" data-testid="manifest-table">
      <thead>
        <tr>
          <th>分发批次</th>
          <th>订阅方</th>
          <th>记录范围</th>
          <th>订阅范围</th>
          <th>记录数</th>
          <th>分发状态</th>
          <th>可领取</th>
          <th>说明</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="task in manifest?.tasks ?? []" :key="task.id" :data-testid="`manifest-row-${task.id}`">
          <td>{{ task.分发批次 || task.id }}</td>
          <td>{{ task.订阅方 || '—' }}</td>
          <td>{{ task.记录标识 || task.记录范围 || '—' }}</td>
          <td>{{ task.订阅范围 || '—' }}</td>
          <td>{{ task.记录数 || '—' }}</td>
          <td>{{ task.分发状态 || task.status || '—' }}</td>
          <td data-testid="manifest-claimable">{{ task.claimable ? '可领取' : (task.claimed ? '已认领' : '不可领取') }}</td>
          <td>{{ task.reason || '—' }}</td>
        </tr>
        <tr v-if="!(manifest?.tasks?.length)">
          <td colspan="8" class="empty-state">
            {{ manifest ? manifest.reason || '当前没有分发清单' : '先回查发布编号' }}
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

import { fetchPublishManifest, fetchPublishReceipt, publishBatch } from '@/api/backend-service'
import type {
  EntryRow,
  PublishManifest,
  PublishReceipt,
  PublishResult,
} from '@/data/types'

const props = defineProps<{ publishRow?: EntryRow | null }>()

const publishRef = ref(String(props.publishRow?.id ?? ''))
const manifest = ref<PublishManifest | null>(null)
const receipt = ref<PublishReceipt | null>(null)
const message = ref('')
const messageOk = ref(false)
const publishRow = ref<EntryRow | null>(props.publishRow ?? null)

const publishId = computed(() => Number(publishRef.value || publishRow.value?.id || 0))

function setMessage(text: string, ok: boolean) {
  message.value = text
  messageOk.value = ok
}

async function applyResult(result: PublishResult) {
  setMessage(result.message, result.ok)
  if (result.version) {
    publishRow.value = {
      ...(publishRow.value ?? { id: publishId.value, status: '待发布', pending: true, abnormal: false }),
      status: result.published ? '已发布' : (publishRow.value?.status ?? '待发布'),
      版本标识: result.version,
      ...(result.entry ?? {}),
    } as EntryRow
  }
  if (result.receipt) receipt.value = result.receipt
  // 发布确认之后同步刷新同版清单，但保留发布提交的确认/失败文案。
  await refreshManifest(true)
}

async function submitPublish() {
  if (!publishId.value) {
    setMessage('请先填写发布编号', false)
    return
  }
  try {
    const result = await publishBatch('golden_publish', publishId.value)
    await applyResult(result)
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '发布提交失败', false)
  }
}

async function loadManifest() {
  if (!publishId.value) {
    setMessage('请先填写发布编号', false)
    return
  }
  try {
    const result = await fetchPublishManifest('golden_publish', publishId.value)
    manifest.value = result
    receipt.value = result.receipt ?? receipt.value
    setMessage(
      result.claimable
        ? `版本 ${result.version} 的分发清单可领取 ${result.claimable_count} 条`
        : `版本 ${result.version || '未确认'}：${result.reason || '暂不可领取'}`,
      result.claimable,
    )
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '发布清单读取失败', false)
  }
}

/** 拉取同版清单但不改动已有提示文案；提交确认后用它刷新表格。 */
async function refreshManifest(silent: boolean) {
  if (!silent || !publishId.value) return
  try {
    const result = await fetchPublishManifest('golden_publish', publishId.value)
    manifest.value = result
  } catch {
    // 清单回查失败不影响上面已展示的提交结果。
  }
}

async function loadReceipt() {
  if (!publishId.value) {
    setMessage('请先填写发布编号', false)
    return
  }
  try {
    const result = await fetchPublishReceipt('golden_publish', publishId.value)
    receipt.value = result.receipt ?? null
    setMessage(
      result.completed
        ? `发布编号 ${result.publish_id} 已完成，版本 ${result.version}`
        : `发布编号 ${result.publish_id} 尚未完成（${result.清单状态 || result.status}）`,
      result.completed,
    )
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '发布回执读取失败', false)
  }
}

function reset() {
  manifest.value = null
  receipt.value = null
  message.value = ''
}

defineExpose({ setPublishRow, loadManifest })
function setPublishRow(row: EntryRow | null) {
  publishRow.value = row
  publishRef.value = String(row?.id ?? '')
  if (publishRef.value) void loadManifest()
}
</script>
