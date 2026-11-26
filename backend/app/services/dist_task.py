"""分发任务领域服务：管理把发布版本分发给订阅方的批次调度、送达确认与失败重试，完成导出动作。

数据链路位置：主数据分发层执行端，负责消费发布版本与订阅过滤，
把送达回执写入回执中心。

领用入口只从发布批次已确认的分发清单里取批次：清单按 ``发布编号`` 精确回查，
不按版本字符串去别的发布批次补数据；批次本身还要带完整的记录范围（记录标识、
记录数）和订阅范围才具备领取资格；同一批次只能被认领一次。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

CLAIM_ACTIONS = {"领取", "领用", "领取分发"}          # 严格一次认领
DISPATCH_ACTIONS = {"开始分发", "分发"}              # 认领并推进/续做


class DistTaskService(BaseModuleService):
    MODULE = "dist_task"
    ENTITY = "分发批次"
    OPERATION_TERM = "导出"
    LIST_FIELDS = ["分发批次", "订阅方", "记录范围", "分发状态", "回执状态"]
    REQUIRED_FIELDS = ["分发批次", "订阅方", "记录范围"]
    STATUS_ORDER = ["排队中", "分发中", "已送达", "分发失败"]
    ACTION_RULES = {"开始分发": "分发中", "重试分发": "分发失败"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：版本分发与回执回收 ----

    def _publish_for_task(self, task: dict[str, Any]) -> dict[str, Any] | None:
        """按发布编号精确回查发布批次；没有发布编号时才回退到版本标识匹配。"""
        raw_id = task.get("发布编号")
        if raw_id not in (None, ""):
            try:
                publish_id = int(raw_id)
            except (TypeError, ValueError):
                return None
            return store.find("golden_publish", publish_id)
        version = str(task.get("版本标识") or "").strip()
        if not version:
            version = str(task.get("记录范围") or "").replace("版本 ", "").strip()
        if not version:
            return None
        return next(
            (p for p in store.rows("golden_publish") if str(p.get("版本标识") or "") == version),
            None,
        )

    def manifest_of(self, publish: dict[str, Any]) -> list[dict[str, Any]]:
        """同一发布编号下的完整分发清单。"""
        publish_id = int(publish.get("id", 0))
        tasks: list[dict[str, Any]] = []
        for task in store.rows(self.MODULE):
            raw_id = task.get("发布编号")
            if raw_id in (None, ""):
                continue
            try:
                linked = int(raw_id)
            except (TypeError, ValueError):
                continue
            if linked == publish_id:
                tasks.append(task)
        return tasks

    def claim_eligibility(self, task: dict[str, Any]) -> dict[str, Any]:
        """判断一条分发批次是否可从完整清单领取；不可领取时给出具体原因。"""
        status = str(task.get("status") or "")
        base = {
            "id": int(task.get("id", 0)),
            "分发批次": task.get("分发批次", ""),
            "订阅方": task.get("订阅方", ""),
            "记录范围": task.get("记录范围", ""),
            "订阅范围": task.get("订阅范围", ""),
            "记录数": task.get("记录数", ""),
            "记录标识": task.get("记录标识", ""),
            "发布编号": task.get("发布编号", ""),
            "版本标识": task.get("版本标识", ""),
            "分发状态": status,
            "领取状态": task.get("领取状态", ""),
            "status": status,
        }
        raw_id = task.get("发布编号")
        publish = None
        if raw_id not in (None, ""):
            try:
                publish = store.find("golden_publish", int(raw_id))
            except (TypeError, ValueError):
                publish = None
        if publish is None:
            return {**base, "claimable": False, "claimed": False,
                    "reason": "该分发批次没有对应的发布编号，不属于任何已确认发布版本"}
        if str(task.get("版本标识") or "") != str(publish.get("版本标识") or ""):
            return {**base, "claimable": False, "claimed": False,
                    "reason": (
                        f"分发批次的版本 {task.get('版本标识') or '未知'} 与发布批次 "
                        f"{publish.get('id')} 的版本 {publish.get('版本标识') or '未知'} 不一致"
                    )}
        if publish.get("status") == "已回滚":
            return {**base, "claimable": False, "claimed": False,
                    "reason": f"发布批次 {publish.get('id')} 已回滚，版本不可领取"}
        if publish.get("清单状态") != "已确认" or publish.get("status") != "已发布":
            return {**base, "claimable": False, "claimed": False,
                    "reason": (
                        f"发布批次 {publish.get('id')} 的分发清单尚未确认"
                        f"（当前 {publish.get('清单状态') or '待登记'}），暂不可领取"
                    )}
        if status == "已送达":
            return {**base, "claimed": True, "claimable": False,
                    "reason": f"分发批次 {task.get('分发批次')} 已送达，不能重复领取"}
        if status == "分发失败":
            return {**base, "claimed": False, "claimable": False,
                    "reason": f"分发批次 {task.get('分发批次')} 分发失败，需走重试分发"}
        if task.get("领取状态") == "已认领" or status == "分发中":
            return {**base, "claimed": True, "claimable": False,
                    "reason": f"分发批次 {task.get('分发批次')} 已被认领，不能重复领取"}
        publish_ids = str(publish.get("记录标识") or "")
        task_ids = str(task.get("记录标识") or "")
        if not task_ids:
            return {**base, "claimable": False, "claimed": False,
                    "reason": f"分发批次 {task.get('分发批次')} 缺少记录范围，清单不完整"}
        if publish_ids and task_ids != publish_ids:
            return {**base, "claimable": False, "claimed": False,
                    "reason": f"分发批次 {task.get('分发批次')} 的记录范围与发布版本不一致"}
        if str(task.get("记录数") or "").strip() != str(publish.get("金记录数") or "").strip():
            return {**base, "claimable": False, "claimed": False,
                    "reason": f"分发批次 {task.get('分发批次')} 的记录数与发布版本不一致"}
        subscriber = str(task.get("订阅方") or "").strip()
        if not subscriber:
            return {**base, "claimable": False, "claimed": False,
                    "reason": "分发行缺少订阅方"}
        if not str(task.get("订阅范围") or "").strip():
            return {**base, "claimable": False, "claimed": False,
                    "reason": f"分发批次 {task.get('分发批次')} 缺少订阅范围，不可领取"}
        sub = next((s for s in store.rows("subscriber_mgr")
                    if str(s.get("订阅方标识") or "") == subscriber), None)
        if sub is None or sub.get("status") != "已生效":
            return {**base, "claimable": False, "claimed": False,
                    "reason": f"订阅方 {subscriber} 未生效，不可领取"}
        return {**base, "claimable": True, "claimed": False, "reason": ""}

    def list_claimable(self, publish_ref: int | str | None = None) -> dict[str, Any]:
        """领取端查询：按发布编号（或版本标识）列出可领取与不可领取的分发批次及原因。"""
        publish: dict[str, Any] | None = None
        if publish_ref in (None, ""):
            return {
                "ok": True,
                "publish_id": None,
                "claimable": [],
                "blocked": [],
                "claimable_count": 0,
                "blocked_count": 0,
                "reason": "请提供发布编号后再查询可领取的分发清单",
            }
        try:
            publish = store.find("golden_publish", int(publish_ref))
        except (TypeError, ValueError):
            publish = None
        if publish is None:
            publish = next(
                (p for p in store.rows("golden_publish")
                 if str(p.get("版本标识") or "") == str(publish_ref)),
                None,
            )
        if publish is None:
            return {
                "ok": True,
                "publish_id": publish_ref,
                "claimable": [],
                "blocked": [],
                "claimable_count": 0,
                "blocked_count": 0,
                "reason": f"没有找到发布编号 {publish_ref} 对应的发布版本",
            }
        claimable: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        for task in self.manifest_of(publish):
            view = self.claim_eligibility(task)
            if view.get("claimable"):
                claimable.append(view)
            else:
                blocked.append(view)
        if publish.get("status") == "已回滚" or publish.get("清单状态") != "已确认":
            reason = (
                f"发布批次 {publish.get('id')} 的分发清单尚未确认"
                f"（当前 {publish.get('清单状态') or '待登记'}），暂不可领取"
            )
        elif not claimable and blocked:
            reason = str(blocked[0]["reason"])
        elif not claimable:
            reason = "没有可领取的分发批次"
        else:
            reason = ""
        return {
            "ok": True,
            "publish_id": int(publish.get("id", 0)),
            "version": str(publish.get("版本标识") or ""),
            "publish_status": publish.get("status", ""),
            "manifest_confirmed": publish.get("清单状态") == "已确认",
            "claimable": claimable,
            "blocked": blocked,
            "claimable_count": len(claimable),
            "blocked_count": len(blocked),
            "reason": reason,
        }

    def claim_batch(self, entry_id: int) -> dict[str, Any]:
        """从发布版本的分发清单领用一条分发批次；完成前不会被误记为已发出。"""
        with store.lock:
            entry = store.find(self.MODULE, entry_id)
            if entry is None:
                return {"ok": False, "message": f"分发批次 {entry_id} 不存在"}
            view = self.claim_eligibility(entry)
            publish = self._publish_for_task(entry)
            if not view.get("claimable"):
                return {
                    "ok": False,
                    "message": str(view.get("reason") or "该分发批次当前不可领取"),
                    "publish_id": int(publish.get("id", 0)) if publish else None,
                    "version": str((publish or {}).get("版本标识") or ""),
                    "task": view,
                }
            now = datetime.now(timezone.utc).isoformat()
            entry["领取状态"] = "已认领"
            entry["认领时间"] = now
            entry["status"] = "分发中"
            entry["分发状态"] = "分发中"
            entry["pending"] = True
            # 只有领到并开始分发后才有“已发出”记录；这里不是送达。
            return {
                "ok": True,
                "message": f"已从发布批次 {publish.get('id')} 领取分发批次 {entry.get('分发批次')}",
                "publish_id": int(publish.get("id", 0)),
                "version": str(publish.get("版本标识") or ""),
                "subscriber": entry.get("订阅方", ""),
                "record_scope": entry.get("记录标识", ""),
                "record_count": entry.get("记录数", ""),
                "subscriber_scope": entry.get("订阅范围", ""),
                "claimed": True,
                "delivered": False,
                "task": self.claim_eligibility(entry),
            }

    def dispatch_batch(self, entry_id: int) -> dict[str, Any]:
        """执行分发：未认领则先认领，已认领则继续推进；未完成清单/送达的批次拒绝。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "分发批次不存在"}
        publish = self._publish_for_task(entry)
        view = self.claim_eligibility(entry)
        if view.get("claimed") and entry.get("status") == "分发中":
            return {
                "ok": True,
                "message": f"分发批次 {entry.get('分发批次')} 已认领，继续分发",
                "subscriber": entry.get("订阅方", ""),
                "scope": entry.get("记录范围"),
                "publish_id": int(publish.get("id", 0)) if publish else None,
                "version": str((publish or {}).get("版本标识") or ""),
                "record_scope": entry.get("记录标识", ""),
                "delivered": False,
            }
        if not view.get("claimable"):
            return {
                "ok": False,
                "message": str(view.get("reason") or "分发批次不可分发"),
                "publish_id": int(publish.get("id", 0)) if publish else None,
                "version": str((publish or {}).get("版本标识") or ""),
                "task": view,
            }
        subscriber = entry.get("订阅方", "")
        sub = next((s for s in store.rows("subscriber_mgr")
                    if s.get("订阅方标识") == subscriber), None)
        if sub is None or sub.get("status") != "已生效":
            return {"ok": False, "message": f"订阅方 {subscriber} 未生效，拒绝分发"}
        entry["领取状态"] = "已认领"
        entry["认领时间"] = entry.get("认领时间") or datetime.now(timezone.utc).isoformat()
        entry["status"] = "分发中"
        entry["分发状态"] = "分发中"
        entry["pending"] = True
        return {
            "ok": True,
            "message": f"分发批次 {entry.get('分发批次')} 已开始分发",
            "subscriber": subscriber,
            "scope": entry.get("记录范围"),
            "publish_id": int(publish.get("id", 0)) if publish else None,
            "version": str((publish or {}).get("版本标识") or ""),
            "record_scope": entry.get("记录标识", ""),
            "delivered": False,
        }

    def confirm_delivery(self, entry_id: int) -> dict[str, Any]:
        """确认送达并向回执中心登记已回收回执。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "分发中":
            return {"ok": False, "message": "批次不在分发中状态"}
        entry["status"] = "已送达"
        entry["分发状态"] = "已送达"
        entry["回执状态"] = "已回收"
        entry["领取状态"] = "已认领"
        entry["送达时间"] = datetime.now(timezone.utc).isoformat()
        entry["pending"] = False
        receipts = store.rows("dist_receipt")
        receipt_id = max((int(r.get("id", 0)) for r in receipts), default=0) + 1
        receipts.append({
            "id": receipt_id,
            "回执编号": f"rc-{entry_id}-{receipt_id}",
            "分发批次": entry.get("分发批次", ""),
            "订阅方": entry.get("订阅方", ""),
            "回执状态": "已回收",
            "差异说明": "",
            "发布编号": entry.get("发布编号", ""),
            "status": "已回收",
            "pending": False,
            "abnormal": False,
        })
        return {"ok": True, "receipt_id": receipt_id,
                "publish_id": entry.get("发布编号"),
                "delivered": True}

    def fail_and_requeue(self, entry_id: int, reason: str) -> dict[str, Any]:
        """标记分发失败，保留断点供重试。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "分发批次不存在"}
        entry["status"] = "分发失败"
        entry["分发状态"] = "分发失败"
        entry["abnormal"] = True
        entry["领取状态"] = "未认领"
        entry["失败原因"] = reason
        return {"ok": False, "message": f"分发失败: {reason}", "retryable": True}

    def retry_dispatch(self, entry_id: int) -> dict[str, Any]:
        """重试分发：失败批次回到清单后重新走领取。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "分发批次不存在"}
        if entry.get("status") != "分发失败":
            return {"ok": False, "message": "只有分发失败的批次可以重试"}
        entry["status"] = "排队中"
        entry["分发状态"] = "排队中"
        entry["领取状态"] = "未认领"
        entry["abnormal"] = False
        entry.pop("失败原因", None)
        entry["pending"] = True
        return {"ok": True, "message": "分发批次已回到清单，可重新领取", "publish_id": entry.get("发布编号")}

    def consume_publish_scope(self, entry_id: int) -> dict[str, Any] | None:
        """读取发布模块的版本范围，校验分发覆盖。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None
        publish = self._publish_for_task(entry)
        if publish is None:
            return None
        return publish

    def resolve_backlog(self, entry_id: int) -> int:
        """按发布批次聚合积压分发任务数。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return 0
        publish = self._publish_for_task(entry)
        if publish is None:
            return 0
        return sum(1 for t in self.manifest_of(publish)
                   if t.get("status") in ("排队中", "分发中"))

    def notify_subscriber_rollback(self, entry_id: int, new_version: str) -> str:
        """回滚后通知订阅方新版本口径。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return "分发批次不存在"
        subscriber = entry.get("订阅方", "")
        for sub in store.rows("subscriber_mgr"):
            if sub.get("订阅方标识") == subscriber:
                sub["pending"] = True
        entry["记录范围"] = f"版本 {new_version}"
        entry["版本标识"] = new_version
        entry["status"] = "排队中"
        entry["领取状态"] = "未认领"
        entry["pending"] = True
        return f"已通知 {subscriber} 切换到 {new_version}"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if action in CLAIM_ACTIONS:
            result = self.claim_batch(entry_id)
            if not result.get("ok"):
                return None, str(result.get("message") or "领取失败")
            return entry, str(result.get("message") or "分发批次已领取")
        if action in DISPATCH_ACTIONS:
            result = self.dispatch_batch(entry_id)
            if not result.get("ok"):
                return None, str(result.get("message") or "分发失败")
            return entry, str(result.get("message") or "分发批次已开始分发")
        if action == "重试分发":
            result = self.retry_dispatch(entry_id)
            if not result.get("ok"):
                return None, str(result.get("message") or "重试失败")
            return entry, str(result.get("message") or "分发批次已重新排队")
        return super().run_action(entry_id, action)
