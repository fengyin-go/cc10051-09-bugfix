"""下游订阅方领域服务：管理下游系统对金记录版本的订阅关系、过滤条件与生效状态，完成导入动作。

数据链路位置：主数据分发层的消费端配置，负责消费发布版本，
按订阅过滤生成分发批次并衔接回执通道。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class SubscriberMgrService(BaseModuleService):
    MODULE = "subscriber_mgr"
    ENTITY = "订阅方"
    OPERATION_TERM = "导入"
    LIST_FIELDS = ["订阅方标识", "订阅实体", "分发格式", "订阅过滤", "生效状态"]
    REQUIRED_FIELDS = ["订阅方标识", "订阅实体", "分发格式"]
    STATUS_ORDER = ["待生效", "已生效", "已暂停"]
    ACTION_RULES = {"生效订阅": "已生效", "暂停订阅": "已暂停"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：订阅生效与分发过滤 ----

    def activate_subscription(self, entry_id: int) -> dict[str, Any]:
        """生效订阅并把订阅方加入分发名单。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "订阅方不存在"}
        if not str(entry.get("分发格式", "")).strip():
            return {"ok": False, "message": "分发格式缺失，不能生效"}
        entry["status"] = "已生效"
        entry["pending"] = False
        return {"ok": True, "subscriber": entry.get("订阅方标识")}

    def apply_filter(self, entry_id: int, publish_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按订阅过滤条件筛选发布记录。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已生效":
            return []
        cond = str(entry.get("订阅过滤", "")).strip()
        if not cond:
            return publish_rows
        field, _, expected = cond.partition("=")
        return [r for r in publish_rows if str(r.get(field, "")) == expected]

    def pause_and_recall(self, entry_id: int) -> dict[str, Any]:
        """暂停订阅并回收未送达的分发批次。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "订阅方不存在"}
        entry["status"] = "已暂停"
        entry["pending"] = True
        recalled = 0
        for task in store.rows("dist_task"):
            if task.get("订阅方") == entry.get("订阅方标识") and task.get("status") == "排队中":
                task["status"] = "分发失败"
                task["abnormal"] = True
                recalled += 1
        return {"ok": True, "recalled_tasks": recalled}

    def consume_publish_versions(self, entry_id: int) -> list[str]:
        """读取订阅实体下对外公布（版本与清单都已确认）的发布版本列表。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return []
        entity = str(entry.get("订阅过滤", "")).partition("=")[2] or entry.get("订阅实体", "")
        return [str(p.get("版本标识", "")) for p in store.rows("golden_publish")
                if p.get("status") == "已发布"
                and p.get("清单状态") == "已确认"
                and entity in str(p.get("实体编码", ""))]

    def produce_dist_batch(self, entry_id: int, version: str) -> int:
        """为订阅方生成分发批次，写入 dist_task（只接受已确认发布版本）。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return 0
        publish = next(
            (p for p in store.rows("golden_publish")
             if str(p.get("版本标识") or "") == str(version)
             and p.get("status") == "已发布"
             and p.get("清单状态") == "已确认"),
            None,
        )
        if publish is None:
            return 0
        tasks = store.rows("dist_task")
        task_id = max((int(r.get("id", 0)) for r in tasks), default=0) + 1
        scope = (str(entry.get("订阅实体") or "").strip()
                 or str(entry.get("订阅过滤") or "").strip())
        tasks.append({
            "id": task_id,
            "分发批次": f"dist-{entry.get('订阅方标识')}-{task_id}",
            "订阅方": entry.get("订阅方标识", ""),
            "记录范围": f"版本 {version}",
            "记录标识": publish.get("记录标识", ""),
            "记录数": publish.get("金记录数", "0"),
            "发布编号": int(publish.get("id", 0)),
            "发布批次": publish.get("发布批次", ""),
            "版本标识": str(version),
            "订阅范围": scope,
            "分发状态": "排队中",
            "回执状态": "未回收",
            "领取状态": "未认领",
            "清单状态": "已确认" if scope else "待补全",
            "status": "排队中",
            "pending": True,
            "abnormal": False,
        })
        return task_id

    def ensure_receipt_collector(self, entry_id: int) -> dict[str, Any]:
        """确认订阅方回执通道可用，登记到回执中心。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "订阅方不存在"}
        receipts = store.rows("dist_receipt")
        receipt_id = max((int(r.get("id", 0)) for r in receipts), default=0) + 1
        receipts.append({
            "id": receipt_id,
            "回执编号": f"rc-{entry.get('订阅方标识')}-{receipt_id}",
            "分发批次": "",
            "订阅方": entry.get("订阅方标识", ""),
            "回执状态": "未回收",
            "差异说明": "通道注册",
            "status": "未回收",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "receipt_id": receipt_id}
