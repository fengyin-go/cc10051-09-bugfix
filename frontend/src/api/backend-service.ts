import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  ClaimableResult,
  ClaimResult,
  EntryRow,
  OverviewResult,
  PageResult,
  PublishManifest,
  PublishReceiptResult,
  PublishResult,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

/** 发布提交：确认版本并登记分发清单；断线后用原发布编号重提可续做。 */
export async function publishBatch(key: string, publishId: number): Promise<PublishResult> {
  const response = await request(`/api/${key}/publish?publish_id=${publishId}`, {
    method: 'POST',
  })
  return await response.json()
}

/** 发布行回查：确认状态、同版分发清单与可领取状态。 */
export async function fetchPublishManifest(
  key: string,
  publishId: number,
): Promise<PublishManifest> {
  const response = await request(`/api/${key}/manifest?publish_id=${publishId}`)
  if (!response.ok) throw new Error('发布清单读取失败')
  return await response.json()
}

/** 发布完成回执：按原发布编号回查提交结果。 */
export async function fetchPublishReceipt(
  key: string,
  publishId: number,
): Promise<PublishReceiptResult> {
  const response = await request(`/api/${key}/receipt?publish_id=${publishId}`)
  if (!response.ok) throw new Error('发布回执读取失败')
  return await response.json()
}

/** 分发领取端：按发布编号取完整分发清单，含不可领取原因。 */
export async function fetchClaimable(
  key: string,
  publishRef: string,
): Promise<ClaimableResult> {
  const query = new URLSearchParams({ publish_id: publishRef }).toString()
  const response = await request(`/api/${key}/claimable?${query}`)
  if (!response.ok) throw new Error('可领取清单读取失败')
  return await response.json()
}

/** 分发领取：认领一条分发批次，未完成前不会记为已发出。 */
export async function claimBatch(key: string, taskId: number): Promise<ClaimResult> {
  const response = await request(`/api/${key}/claim?task_id=${taskId}`, {
    method: 'POST',
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}
