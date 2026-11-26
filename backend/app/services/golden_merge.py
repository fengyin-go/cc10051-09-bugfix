"""金记录合并领域服务：管理重复候选组按生还规则合并成金记录的任务与冲突处置。

数据链路位置：主数据加工层第三环，负责消费重复检测结果与复核结论，
把金记录派发给发布批次与交叉映射。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class GoldenMergeService(BaseModuleService):
    MODULE = "golden_merge"
    ENTITY = "合并任务"
    OPERATION_TERM = "合并"
    LIST_FIELDS = ["合并任务", "实体编码", "候选组", "生还规则", "合并结果"]
    REQUIRED_FIELDS = ["合并任务", "实体编码", "候选组"]
    STATUS_ORDER = ["待合并", "已合并", "合并冲突"]
    ACTION_RULES = {"执行合并": "已合并", "转人工": "合并冲突"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：金记录合并与发布衔接 ----

    def execute_with_survivor(self, entry_id: int) -> dict[str, Any]:
        """按生还规则执行合并，产出金记录标识；重复执行沿用已有金记录。"""
        with store.lock:
            entry = store.find(self.MODULE, entry_id)
            if entry is None:
                return {"ok": False, "message": "合并任务不存在"}
            if entry.get("status") == "已合并" and str(entry.get("合并结果") or "").strip():
                return {
                    "ok": True,
                    "golden_id": str(entry.get("合并结果")),
                    "survivor_rule": str(entry.get("生还规则", "最新优先")),
                    "already_merged": True,
                }
            if entry.get("status") != "待合并":
                return {"ok": False, "message": "合并任务不在待合并状态"}
            rule = str(entry.get("生还规则", "最新优先"))
            golden_id = f"GR-{entry.get('实体编码')}-{entry_id:04d}"
            if rule == "置信度最高":
                golden_id += "-C"
            entry["合并结果"] = golden_id
            entry["status"] = "已合并"
            entry["pending"] = False
            entry["abnormal"] = False
            return {"ok": True, "golden_id": golden_id, "survivor_rule": rule}

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if action == "执行合并":
            result = self.execute_with_survivor(entry_id)
            if not result.get("ok"):
                return None, str(result.get("message") or "合并失败")
            message = (
                f"合并任务已合并，金记录 {result.get('golden_id')}"
                if not result.get("already_merged")
                else f"合并任务此前已合并，沿用金记录 {result.get('golden_id')}"
            )
            return entry, message
        return super().run_action(entry_id, action)

    def resolve_conflict(self, entry_id: int, conclusion: str) -> str:
        """按复核结论解决合并冲突。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "合并冲突":
            return "任务不在冲突状态"
        if conclusion == "通过":
            entry["status"] = "已合并"
            entry["合并结果"] = f"GR-{entry.get('实体编码')}-{entry_id:04d}-R"
            entry["pending"] = False
        else:
            entry["status"] = "待合并"
            entry["pending"] = True
        return f"冲突已按 {conclusion} 处理"

    def consume_review_results(self, entry_id: int) -> list[dict[str, Any]]:
        """读取合并复核队列的结论，回写冲突处理依据。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return []
        group = entry.get("候选组", "")
        return [r for r in store.rows("merge_review")
                if r.get("候选组") == group and r.get("status") in ("已通过", "已驳回")]

    def queue_publish(self, entry_id: int) -> dict[str, Any]:
        """把已合并任务排入金记录发布队列。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已合并":
            return {"ok": False, "message": "任务未合并完成，不能发布"}
        publishes = store.rows("golden_publish")
        publish_id = max((int(r.get("id", 0)) for r in publishes), default=0) + 1
        publishes.append({
            "id": publish_id,
            "发布批次": f"pub-{entry.get('实体编码')}-{publish_id}",
            "实体编码": entry.get("实体编码", ""),
            "金记录数": "1",
            "发布时间": datetime.now(timezone.utc).isoformat(),
            "版本标识": f"v{publish_id}",
            "status": "待发布",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "publish_id": publish_id}

    def build_cross_map(self, entry_id: int, source_ids: list[str]) -> int:
        """为合并后的金记录建立源标识交叉映射。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已合并":
            return 0
        golden_id = entry.get("合并结果", "")
        maps = store.rows("id_cross_map")
        mapped = 0
        for src in source_ids:
            map_id = max((int(r.get("id", 0)) for r in maps), default=0) + 1
            maps.append({
                "id": map_id,
                "映射批次": f"map-{entry_id}-{map_id}",
                "源系统标识": src,
                "金记录标识": golden_id,
                "映射置信度": "1.0",
                "映射状态": "已映射",
                "status": "已映射",
                "pending": False,
                "abnormal": False,
            })
            mapped += 1
        return mapped

    def record_lineage(self, entry_id: int) -> str | None:
        """登记合并任务到发布批次的血缘边。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None
        edges = store.rows("lineage_graph")
        edge_id = max((int(r.get("id", 0)) for r in edges), default=0) + 1
        edges.append({
            "id": edge_id,
            "血缘边标识": f"edge-merge-{edge_id}",
            "上游节点": entry.get("合并任务", ""),
            "下游节点": "golden_publish",
            "变换规则": str(entry.get("生还规则", "最新优先")),
            "登记时间": datetime.now(timezone.utc).isoformat(),
            "status": "已登记",
            "pending": False,
            "abnormal": False,
        })
        return f"edge-merge-{edge_id}"
