/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

/** 分发清单条目：发布行回查与领取端共用。 */
export type ManifestTask = {
  id: number
  分发批次?: string
  订阅方?: string
  记录范围?: string
  订阅范围?: string
  记录数?: string
  记录标识?: string
  发布编号?: number | string
  版本标识?: string
  分发状态?: string
  领取状态?: string
  status?: string
  claimable?: boolean
  claimed?: boolean
  reason?: string
}

export type PublishTaskView = ManifestTask

export type PublishResult = {
  ok: boolean
  message: string
  publish_id?: number
  version?: string
  reason?: string
  tasks?: PublishTaskView[]
  created?: number[]
  manifest_count?: number
  receipt?: PublishReceipt | null
  published?: boolean
  already_published?: boolean
  resumed?: boolean
  already_confirmed?: boolean
  entry?: EntryRow
}

export type PublishReceipt = {
  回执编号?: string
  发布编号?: number | string
  版本标识?: string
  状态?: string
  金记录数?: string
  清单数?: string
  清单摘要?: string
  完成时间?: string
  [field: string]: string | number | undefined
}

export type PublishManifest = {
  ok: boolean
  publish_id: number
  version: string
  confirmed: boolean
  manifest_confirmed: boolean
  claimable: boolean
  claimable_count: number
  claimed_count: number
  reason: string
  publish: Record<string, string | number>
  tasks: PublishTaskView[]
  receipt: PublishReceipt | null
}

export type PublishReceiptResult = {
  ok: boolean
  publish_id: number
  version: string
  status: string
  发布确认?: string
  清单状态?: string
  completed: boolean
  receipt: PublishReceipt | null
}

export type ClaimableResult = {
  ok: boolean
  publish_id: number | string | null
  version?: string
  publish_status?: string
  manifest_confirmed?: boolean
  claimable: PublishTaskView[]
  blocked: PublishTaskView[]
  claimable_count: number
  blocked_count: number
  reason: string
}

export type ClaimResult = {
  ok: boolean
  message: string
  publish_id?: number | null
  version?: string
  subscriber?: string
  record_scope?: string
  record_count?: string
  subscriber_scope?: string
  claimed?: boolean
  delivered?: boolean
  task?: PublishTaskView
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}
