"""金记录发布领域服务：管理金记录版本的发布批次、版本标识、扇出分发与回滚衔接，完成入库动作。

数据链路位置：主数据发布层，负责消费合并产物，把发布版本
扇出给订阅方并衔接版本回滚任务。

发布过程分两段：
1. 确认版本：只消费同一实体里真正完成合并（``已合并`` 且带 ``合并结果``）的记录，
   快照记录标识并锁定版本标识；重复提交沿用已确认版本，不再改写。
2. 确认分发清单：为每个生效订阅方生成一条范围完整的分发批次；只有清单全部
   确认之后发布批次才对外公布为 ``已发布``。断线重提会从第一段或第二段的断点继续。
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

PUBLISH_ACTIONS = {"发布批次", "发布", "确认发布", "提交发布"}
ROLLBACK_ACTIONS = {"回滚批次", "回滚"}


class GoldenPublishService(BaseModuleService):
    MODULE = "golden_publish"
    ENTITY = "发布批次"
    OPERATION_TERM = "入库"
    LIST_FIELDS = ["发布批次", "实体编码", "金记录数", "发布时间", "版本标识"]
    REQUIRED_FIELDS = ["发布批次", "实体编码", "金记录数"]
    STATUS_ORDER = ["待发布", "已发布", "已回滚"]
    ACTION_RULES = {"发布批次": "已发布", "回滚批次": "已回滚"}
    NEGATIVE_ACTIONS = ["回滚批次"]

    # ---- 业务扩展：金记录发布与分发衔接 ----

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _confirmed(self, entry: dict[str, Any]) -> bool:
        return entry.get("发布确认") == "已确认" and bool(str(entry.get("版本标识") or "").strip())

    def merged_inputs(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        """发布输入：同一实体里已真正完成合并并产出金记录标识的任务。

        只有 ``status == 已合并`` 且 ``合并结果`` 非空的记录才算完成合并；
        实体编码必须与发布批次一致，不能拿别的实体的合并产物顶替。
        """
        entity = str(entry.get("实体编码") or "").strip()
        if not entity:
            return []
        merged: list[dict[str, Any]] = []
        for task in store.rows("golden_merge"):
            if str(task.get("实体编码") or "").strip() != entity:
                continue
            if task.get("status") != "已合并":
                continue
            golden_id = str(task.get("合并结果") or "").strip()
            if not golden_id:
                continue
            merged.append(task)
        return merged

    def _snapshot(self, merged: list[dict[str, Any]]) -> tuple[str, str]:
        golden_ids: list[str] = []
        for task in merged:
            golden_id = str(task.get("合并结果") or "").strip()
            if golden_id and golden_id not in golden_ids:
                golden_ids.append(golden_id)
        return ",".join(golden_ids), str(len(golden_ids))

    def _assign_version(self, entry: dict[str, Any]) -> str:
        """生成一个只会用一次的版本标识：同一批次确认后不再重新生成。"""
        entry_id = int(entry.get("id", 0))
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        base = f"pub-v{entry_id}-{day}"
        existing = {str(p.get("版本标识") or "") for p in store.rows(self.MODULE)}
        version = base
        suffix = 1
        while version in existing:
            version = f"{base}-{suffix}"
            suffix += 1
        return version

    def _active_subscribers(self) -> list[dict[str, Any]]:
        return [s for s in store.rows("subscriber_mgr") if s.get("status") == "已生效"]

    @staticmethod
    def subscriber_scope(subscriber: dict[str, Any]) -> str:
        """订阅范围：订阅实体优先，其次订阅过滤；两者都空视为没有订阅范围。"""
        for field in ("订阅实体", "订阅过滤"):
            value = str(subscriber.get(field) or "").strip()
            if value:
                return value
        return ""

    def manifest_tasks(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        """按发布编号精确回查本批次的分发清单，不用版本字符串去别的批次凑数据。"""
        publish_id = int(entry.get("id", 0))
        tasks: list[dict[str, Any]] = []
        for task in store.rows("dist_task"):
            raw_id = task.get("发布编号")
            if raw_id in (None, ""):
                continue
            try:
                linked_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if linked_id == publish_id:
                tasks.append(task)
        return tasks

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记发布批次时清掉表里带的旧版本标识，等确认版本后统一写入。"""
        entry, missing = super().create_entry(values)
        if entry is None:
            return None, missing
        entry["发布时间"] = ""
        entry["版本标识"] = ""
        entry["发布确认"] = "未确认"
        entry["清单状态"] = "待登记"
        entry["清单数"] = "0"
        entry["记录标识"] = ""
        entry["记录数"] = ""
        entry["记录范围"] = ""
        return entry, []

    # ---- 第一段：确认版本 ----

    def begin_publish(self, entry_id: int) -> dict[str, Any]:
        """确认发布版本并快照记录范围；断线后可重复调用，版本标识保持不变。"""
        with store.lock:
            entry = store.find(self.MODULE, entry_id)
            if entry is None:
                return {"ok": False, "message": f"发布批次 {entry_id} 不存在或已归档"}
            if entry.get("status") == "已回滚":
                return {
                    "ok": False,
                    "message": f"发布批次 {entry_id} 已回滚，不能重新变更版本标识",
                    "publish_id": entry_id,
                    "version": entry.get("版本标识"),
                }
            if self._confirmed(entry):
                return {
                    "ok": True,
                    "message": f"版本 {entry.get('版本标识')} 已确认，沿用原版本标识",
                    "publish_id": entry_id,
                    "version": str(entry.get("版本标识")),
                    "resumed": True,
                    "already_confirmed": True,
                    "entry": entry,
                }
            merged = self.merged_inputs(entry)
            if not merged:
                # 没有真正完成合并的输入时清掉表里可能残留的旧版本标识，不对外沿用。
                entry["版本标识"] = ""
                entry["发布时间"] = ""
                entry["记录标识"] = ""
                entry["记录数"] = ""
                entry["发布确认"] = "未确认"
                entry["清单状态"] = "待登记"
                entry["清单数"] = "0"
                return {
                    "ok": False,
                    "message": (
                        f"发布批次 {entry_id} 没有属于实体 {entry.get('实体编码', '')} 的已完成合并记录，"
                        "不能生成发布版本"
                    ),
                    "publish_id": entry_id,
                    "reason": "no_merged_input",
                }
            golden_ids, count = self._snapshot(merged)
            version = self._assign_version(entry)
            entry["版本标识"] = version
            entry["发布时间"] = self._now()
            entry["金记录数"] = count
            entry["记录数"] = count
            entry["记录标识"] = golden_ids
            entry["记录范围"] = f"版本 {version}"
            entry["发布号"] = str(entry_id)
            entry["发布确认"] = "已确认"
            entry["清单状态"] = "待登记"
            entry["清单数"] = "0"
            entry.pop("完成回执", None)
            entry["status"] = "待发布"
            entry["pending"] = True
            entry["abnormal"] = False
            return {
                "ok": True,
                "message": f"发布版本 {version} 已确认，等待登记分发清单",
                "publish_id": entry_id,
                "version": version,
                "golden_ids": golden_ids.split(",") if golden_ids else [],
                "golden_count": int(count),
                "entry": entry,
            }

    # ---- 第二段：登记并确认分发清单 ----

    def confirm_manifest(self, entry_id: int) -> dict[str, Any]:
        """为生效订阅方补齐分发清单；清单完整后才把发布批次公布为 ``已发布``。

        重复调用只补缺失的分发批次，不覆盖已认领/已送达的批次。
        """
        with store.lock:
            entry = store.find(self.MODULE, entry_id)
            if entry is None:
                return {"ok": False, "message": f"发布批次 {entry_id} 不存在或已归档"}
            if not self._confirmed(entry):
                return {
                    "ok": False,
                    "message": "发布版本尚未确认，不能登记分发清单",
                    "publish_id": entry_id,
                }
            if entry.get("status") == "已回滚":
                return {
                    "ok": False,
                    "message": "发布批次已回滚，不能重新确认分发清单",
                    "publish_id": entry_id,
                }
            if entry.get("清单状态") == "已确认" and entry.get("status") == "已发布":
                tasks = self.manifest_tasks(entry)
                return {
                    "ok": True,
                    "message": self._done_message(entry, tasks, resumed=True),
                    "publish_id": entry_id,
                    "version": str(entry.get("版本标识")),
                    "tasks": tasks,
                    "manifest_count": len(tasks),
                    "already_published": True,
                    "entry": entry,
                }

            version = str(entry.get("版本标识") or "")
            golden_ids = str(entry.get("记录标识") or "")
            count = str(entry.get("金记录数") or "0")
            subscribers = self._active_subscribers()
            tasks = self.manifest_tasks(entry)
            by_subscriber = {str(t.get("订阅方") or ""): t for t in tasks}
            created: list[int] = []
            incomplete_reason = ""
            for subscriber in subscribers:
                subscriber_id = str(subscriber.get("订阅方标识") or "").strip()
                scope = self.subscriber_scope(subscriber)
                if not subscriber_id:
                    incomplete_reason = "存在缺少标识的生效订阅方，分发清单无法确认"
                    continue
                task = by_subscriber.get(subscriber_id)
                if task is None:
                    task_id = max(
                        (int(row.get("id", 0)) for row in store.rows("dist_task")),
                        default=0,
                    ) + 1
                    task = {
                        "id": task_id,
                        "分发批次": f"dist-{entry_id}-{task_id}",
                        "订阅方": subscriber_id,
                        "记录范围": f"版本 {version}",
                        "记录标识": golden_ids,
                        "记录数": count,
                        "发布编号": int(entry_id),
                        "发布批次": entry.get("发布批次", ""),
                        "版本标识": version,
                        "订阅范围": scope,
                        "分发状态": "排队中",
                        "回执状态": "未回收",
                        "领取状态": "未认领",
                        "清单状态": "已确认" if scope else "待补全",
                        "status": "排队中",
                        "pending": True,
                        "abnormal": False,
                    }
                    if not scope:
                        task["不完整原因"] = f"订阅方 {subscriber_id} 没有订阅范围"
                    store.rows("dist_task").append(task)
                    created.append(task_id)
                elif task.get("领取状态") != "已认领" and task.get("status") not in ("已送达",):
                    # 只补齐尚未被认领的批次，不覆盖已经领走的分发。
                    task["发布编号"] = int(entry_id)
                    task["发布批次"] = entry.get("发布批次", "")
                    task["版本标识"] = version
                    task["记录范围"] = f"版本 {version}"
                    task["记录标识"] = golden_ids
                    task["记录数"] = count
                    task["订阅范围"] = scope
                    if scope:
                        task["清单状态"] = "已确认"
                        task.pop("不完整原因", None)
                    else:
                        task["清单状态"] = "待补全"
                        task["不完整原因"] = f"订阅方 {subscriber_id} 没有订阅范围"
                if not scope:
                    incomplete_reason = f"订阅方 {subscriber_id} 没有订阅范围，分发清单缺少订阅范围"
                elif task.get("清单状态") != "已确认":
                    incomplete_reason = f"分发批次 {task.get('分发批次')} 尚未确认"

            if not subscribers:
                incomplete_reason = "没有生效订阅方，无法确认分发清单"

            complete = (
                bool(subscribers)
                and not incomplete_reason
                and all(t.get("清单状态") == "已确认" for t in self.manifest_tasks(entry))
                and all(
                    str(t.get("记录标识") or "") == golden_ids
                    and str(t.get("记录数") or "") == count
                    and str(t.get("订阅方") or "").strip()
                    for t in self.manifest_tasks(entry)
                )
            )

            tasks = self.manifest_tasks(entry)
            if complete:
                entry["清单状态"] = "已确认"
                entry["清单确认时间"] = self._now()
                entry["清单数"] = str(len(tasks))
                entry["完成回执"] = self._build_receipt(entry, tasks)
                entry["status"] = "已发布"
                entry["pending"] = False
                entry["abnormal"] = False
                return {
                    "ok": True,
                    "message": self._done_message(entry, tasks, resumed=bool(created) and False),
                    "publish_id": entry_id,
                    "version": version,
                    "tasks": tasks,
                    "created": created,
                    "manifest_count": len(tasks),
                    "receipt": entry["完成回执"],
                    "published": True,
                    "entry": entry,
                }

            entry["清单状态"] = "待补全"
            entry["清单数"] = str(len(tasks))
            entry["status"] = "待发布"
            entry["pending"] = True
            return {
                "ok": False,
                "message": f"版本 {version} 已确认，但分发清单未就绪：{incomplete_reason}",
                "publish_id": entry_id,
                "version": version,
                "reason": incomplete_reason or "manifest_incomplete",
                "tasks": tasks,
                "created": created,
                "manifest_count": len(tasks),
                "published": False,
                "entry": entry,
            }

    def _build_receipt(self, entry: dict[str, Any], tasks: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "回执编号": f"pub-receipt-{int(entry.get('id', 0))}",
            "发布编号": int(entry.get("id", 0)),
            "版本标识": str(entry.get("版本标识") or ""),
            "状态": "已完成",
            "金记录数": str(entry.get("金记录数") or "0"),
            "清单数": str(len(tasks)),
            "清单摘要": ",".join(str(t.get("分发批次") or "") for t in tasks),
            "完成时间": self._now(),
        }

    def _done_message(self, entry: dict[str, Any], tasks: list[dict[str, Any]], *, resumed: bool) -> str:
        prefix = "重复提交已续做，" if resumed else ""
        return (
            f"{prefix}发布批次 {entry.get('发布批次', entry.get('id'))} 版本 {entry.get('版本标识')} "
            f"已发布，同版分发清单 {len(tasks)} 条"
        )

    # ---- 对外动作入口 ----

    def publish_batch(self, entry_id: int) -> dict[str, Any]:
        """完整发布：先确认版本，再登记并确认分发清单。重复请求走幂等分支。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"发布批次 {entry_id} 不存在或已归档"}
        if entry.get("status") == "已回滚":
            return {
                "ok": False,
                "message": f"发布批次 {entry_id} 已回滚，不能再次发布",
                "publish_id": entry_id,
            }
        if self._confirmed(entry) and entry.get("清单状态") == "已确认" and entry.get("status") == "已发布":
            tasks = self.manifest_tasks(entry)
            return {
                "ok": True,
                "message": self._done_message(entry, tasks, resumed=True),
                "publish_id": entry_id,
                "version": str(entry.get("版本标识")),
                "tasks": tasks,
                "manifest_count": len(tasks),
                "receipt": entry.get("完成回执"),
                "already_published": True,
                "published": True,
                "entry": entry,
            }
        begun = self.begin_publish(entry_id)
        if not begun.get("ok"):
            return begun
        confirmed = self.confirm_manifest(entry_id)
        # 版本已确认但清单尚未完整时，仍把发布批次回给页面，方便操作员从原编号续提。
        confirmed.setdefault("version", begun.get("version"))
        return confirmed

    def release_version(self, entry_id: int) -> dict[str, Any]:
        """发布金记录版本，写发布时间与版本标识（幂等，不更换已确认版本）。"""
        result = self.publish_batch(entry_id)
        entry = store.find(self.MODULE, entry_id)
        if not result.get("ok"):
            return {"ok": False, "message": result.get("message", "发布失败")}
        return {
            "ok": True,
            "version": result.get("version", str((entry or {}).get("版本标识") or "")),
            "released_at": str((entry or {}).get("发布时间") or ""),
            "published": bool((entry or {}).get("status") == "已发布"),
            "manifest_count": result.get("manifest_count", 0),
        }

    def fanout_to_subscribers(self, entry_id: int) -> list[int]:
        """把已确认版本扇出给全部生效订阅方，生成缺哪补哪的分发批次。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or not self._confirmed(entry) or entry.get("status") == "已回滚":
            return []
        before = {int(t.get("id", 0)) for t in self.manifest_tasks(entry)}
        self.confirm_manifest(entry_id)
        after = self.manifest_tasks(entry)
        return [int(t.get("id", 0)) for t in after if int(t.get("id", 0)) not in before]

    # ---- 查询：发布确认、同版分发清单与完成回执 ----

    def get_manifest(self, entry_id: int) -> dict[str, Any]:
        """回查发布行与同版分发清单；未确认时给出不可领取的原因。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"发布批次 {entry_id} 不存在或已归档"}
        dist_service = _dist_service()
        tasks = self.manifest_tasks(entry)
        views = [dist_service.claim_eligibility(task) for task in tasks]
        confirmed = self._confirmed(entry)
        manifest_confirmed = entry.get("清单状态") == "已确认" and entry.get("status") == "已发布"
        reason = ""
        if not confirmed:
            reason = "发布版本尚未确认"
        elif entry.get("status") == "已回滚":
            reason = f"发布批次 {entry_id} 已回滚，版本不可领取"
        elif not tasks:
            reason = (
                f"发布批次 {entry_id} 的版本 {entry.get('版本标识')} 已确认，"
                f"但分发清单尚未确认（还没有可登记的分发批次）"
            )
        elif not manifest_confirmed:
            incomplete = next(
                (str(t.get("不完整原因") or "") for t in tasks if t.get("不完整原因")), "")
            reason = (
                f"发布批次 {entry_id} 的版本 {entry.get('版本标识')} 已确认，"
                f"分发清单尚未确认（{entry.get('清单状态') or '待登记'}）"
                + (f"：{incomplete}" if incomplete else "")
            )
        if not reason and not any(v.get("claimable") for v in views):
            reason = next((str(v.get("reason")) for v in views if not v.get("claimable")), "")
        return {
            "ok": True,
            "publish": {
                "id": int(entry.get("id", 0)),
                "发布批次": entry.get("发布批次", ""),
                "实体编码": entry.get("实体编码", ""),
                "金记录数": entry.get("金记录数", ""),
                "发布时间": entry.get("发布时间", ""),
                "版本标识": entry.get("版本标识", ""),
                "status": entry.get("status", ""),
                "发布确认": entry.get("发布确认", ""),
                "清单状态": entry.get("清单状态", ""),
                "清单数": entry.get("清单数", "0"),
                "记录标识": entry.get("记录标识", ""),
            },
            "publish_id": entry_id,
            "version": str(entry.get("版本标识") or ""),
            "confirmed": confirmed,
            "manifest_confirmed": manifest_confirmed,
            "claimable": manifest_confirmed and any(v.get("claimable") for v in views),
            "claimable_count": sum(1 for v in views if v.get("claimable")),
            "claimed_count": sum(1 for v in views if v.get("claimed")),
            "reason": reason,
            "tasks": views,
            "receipt": entry.get("完成回执"),
        }

    def get_receipt(self, entry_id: int) -> dict[str, Any]:
        """完成回执：发布断线后按原编号回查提交结果。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"发布批次 {entry_id} 不存在或已归档"}
        receipt = entry.get("完成回执")
        return {
            "ok": True,
            "publish_id": entry_id,
            "version": str(entry.get("版本标识") or ""),
            "status": entry.get("status", ""),
            "发布确认": entry.get("发布确认", ""),
            "清单状态": entry.get("清单状态", ""),
            "completed": bool(receipt),
            "receipt": receipt,
        }

    def rollback_version(self, entry_id: int, reason: str) -> dict[str, Any]:
        """回滚发布版本并触发版本回滚任务。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已发布":
            return {"ok": False, "message": "批次未发布，无法回滚"}
        entry["status"] = "已回滚"
        entry["abnormal"] = True
        rollbacks = store.rows("version_rollback")
        rb_id = max((int(r.get("id", 0)) for r in rollbacks), default=0) + 1
        rollbacks.append({
            "id": rb_id,
            "回滚任务": f"rb-{entry_id}-{rb_id}",
            "目标版本": entry.get("版本标识", ""),
            "回滚原因": reason,
            "执行状态": "待执行",
            "影响订阅方": "",
            "status": "待执行",
            "pending": True,
            "abnormal": True,
        })
        for task in self.manifest_tasks(entry):
            if task.get("status") in ("排队中", "分发中"):
                task["status"] = "分发失败"
                task["分发状态"] = "分发失败"
                task["abnormal"] = True
        return {"ok": True, "rollback_id": rb_id, "message": "回滚任务已创建"}

    def consume_merge_output(self, entry_id: int) -> list[dict[str, Any]]:
        """读取合并模块产出的金记录，作为发布输入。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return []
        return self.merged_inputs(entry)

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if action in PUBLISH_ACTIONS:
            result = self.publish_batch(entry_id)
            if not result.get("ok"):
                return None, str(result.get("message") or "发布失败")
            return entry, str(result.get("message") or "发布批次已发布")
        if action in ROLLBACK_ACTIONS:
            result = self.rollback_version(entry_id, "人工回滚")
            if not result.get("ok"):
                return None, str(result.get("message") or "回滚失败")
            return entry, str(result.get("message") or "批次已回滚")
        return super().run_action(entry_id, action)

    def emit_stats_snapshot(self, entry_id: int) -> int:
        """发布完成后向治理看板输黄金记录数快照。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return 0
        stats = store.rows("stats_overview")
        stat_id = max((int(r.get("id", 0)) for r in stats), default=0) + 1
        stats.append({
            "id": stat_id,
            "统计时间": datetime.now(timezone.utc).isoformat(),
            "实体编码": entry.get("实体编码", ""),
            "金记录数": str(entry.get("金记录数", "0")),
            "重复率": "0%",
            "分发延迟": "0",
            "status": "正常",
            "pending": False,
            "abnormal": False,
        })
        return stat_id

    def register_publish_lineage(self, entry_id: int) -> str | None:
        """登记发布批次到分发模块的血缘边。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None
        edges = store.rows("lineage_graph")
        edge_id = max((int(r.get("id", 0)) for r in edges), default=0) + 1
        edges.append({
            "id": edge_id,
            "血缘边标识": f"edge-pub-{edge_id}",
            "上游节点": entry.get("发布批次", ""),
            "下游节点": "dist_task",
            "变换规则": "版本分发",
            "登记时间": datetime.now(timezone.utc).isoformat(),
            "status": "已登记",
            "pending": False,
            "abnormal": False,
        })
        return f"edge-pub-{edge_id}"


def _dist_service():
    """运行时取分发服务，避免与 dist_task 模块形成导入环。"""
    from app.services import service_for

    return service_for("dist_task")
