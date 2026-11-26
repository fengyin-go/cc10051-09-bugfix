<template>
  <section class="panel" data-testid="dist-claim">
    <header class="panel-head">
      <div>
        <h3>分发领取</h3>
        <p class="panel-hint">
          按发布编号取完整分发清单；不可领取时显示具体原因，未完成的申请不会记为已发出。
        </p>
      </div>
    </header>

    <div class="panel-toolbar">
      <label class="filter-item">
        <span>发布编号</span>
        <input v-model="publishRef" data-testid="claim-publish-ref" placeholder="填写发布行 id" />
      </label>
      <button class="btn" data-testid="query-claimable" type="button" @click="queryClaimable">
        查可领取清单
      </button>
    </div>

    <p v-if="message" :class="['panel-message', messageOk ? 'ok-text' : 'error-text']" data-testid="claim-message">
      {{ message }}
    </p>

    <p v-if="claimable?.reason && !claimable?.claimable_count" class="panel-hint" data-testid="claim-reason">
      {{ claimable.reason }}
    </p>

    <table class="data-table" data-testid="claim-table">
      <thead>
        <tr>
          <th>分发批次</th>
          <th>订阅方</th>
          <th>记录范围</th>
          <th>订阅范围</th>
          <th>记录数</th>
          <th>分发状态</th>
          <th>领取</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="task in [...(claimable?.claimable ?? []), ...(claimable?.blocked ?? [])]"
          :key="task.id"
          :data-testid="`claim-row-${task.id}`"
        >
          <td>{{ task.分发批次 || task.id }}</td>
          <td>{{ task.订阅方 || '—' }}</td>
          <td>{{ task.记录标识 || task.记录范围 || '—' }}</td>
          <td>{{ task.订阅范围 || '—' }}</td>
          <td>{{ task.记录数 || '—' }}</td>
          <td>{{ task.分发状态 || task.status || '—' }}</td>
          <td>
            <button
              v-if="task.claimable"
              class="link"
              type="button"
              :data-testid="`claim-action-${task.id}`"
              @click="claim(task.id)"
            >
              领取
            </button>
            <span v-else class="panel-hint" :data-testid="`claim-blocked-${task.id}`">
              {{ task.claimed ? '已认领' : task.reason || '不可领取' }}
            </span>
          </td>
        </tr>
        <tr v-if="!totalRows">
          <td colspan="7" class="empty-state">先按发布编号查询可领取的分发清单</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

import { claimBatch, fetchClaimable } from '@/api/backend-service'
import type { ClaimableResult } from '@/data/types'

const emit = defineEmits<{ (event: 'claimed'): void }>()

const publishRef = ref('')
const claimable = ref<ClaimableResult | null>(null)
const message = ref('')
const messageOk = ref(false)
const totalRows = computed(
  () => (claimable.value?.claimable.length ?? 0) + (claimable.value?.blocked.length ?? 0),
)

function setMessage(text: string, ok: boolean) {
  message.value = text
  messageOk.value = ok
}

async function queryClaimable() {
  if (!publishRef.value.trim()) {
    setMessage('请先填写发布编号', false)
    return
  }
  try {
    const result = await fetchClaimable('dist_task', publishRef.value.trim())
    claimable.value = result
    setMessage(
      result.claimable_count
        ? `发布编号 ${result.publish_id} 可领取 ${result.claimable_count} 条（版本 ${result.version}）`
        : `发布编号 ${result.publish_id} 暂不可领取：${result.reason}`,
      Boolean(result.claimable_count),
    )
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '可领取清单读取失败', false)
  }
}

async function claim(taskId: number) {
  try {
    const result = await claimBatch('dist_task', taskId)
    setMessage(result.message, result.ok)
    if (result.ok) emit('claimed')
    await queryClaimable()
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '领取失败', false)
  }
}

defineExpose({ setPublishRef })
function setPublishRef(ref: string) {
  publishRef.value = ref
  claimable.value = null
}
</script>
