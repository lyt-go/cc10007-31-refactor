"""策略库 / 实时决策重构的回归用例。

覆盖三件事，全部通过 FastAPI 的接口入口执行，不依赖外部测试框架：

1. 既有行为不变：列表、详情、动作响应、导出与运营概览的返回结构与存量数据逐字段一致。
2. 执行明细：同一触发对象只保留一个最终动作，落败会话逐条解释，重复登记收敛为一条记录。
3. 可追溯：发布记录与规则命中结果共享同一生效会话与数据版本，失败整体回退。

运行：``.venv/bin/python -m tests.test_decision``（在 backend 目录下）。
"""
from __future__ import annotations

import copy
import threading
import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.services.decision import ledger
from app.services.realtime import RealtimeService
from app.services.tacticlib import TacticlibService
from app.store import store

ORIGINAL_REALTIME = copy.deepcopy(store.rows("realtime"))
ORIGINAL_TACTIC = copy.deepcopy(store.rows("tacticlib"))


def _reset() -> None:
    store.rows("realtime")[:] = copy.deepcopy(ORIGINAL_REALTIME)
    store.rows("tacticlib")[:] = copy.deepcopy(ORIGINAL_TACTIC)
    for table in ("realtime", "tacticlib"):
        original = ORIGINAL_REALTIME if table == "realtime" else ORIGINAL_TACTIC
        store.rows(table)[:] = copy.deepcopy(original)


class ExistingBehaviorTest(unittest.TestCase):
    """既有列表、详情、动作响应与对外结构保持不变。"""

    def setUp(self) -> None:
        _reset()
        self.client = TestClient(app)

    def test_list_shape_and_seed_rows(self) -> None:
        payload = self.client.get("/api/tacticlib").json()
        self.assertEqual(set(payload), {"items", "total", "page", "size"})
        self.assertEqual(payload["total"], 3)
        self.assertEqual(payload["items"][0], {
            "id": 1, "status": "草稿", "pending": True, "abnormal": False,
            "策略编号": "TACT-0001", "策略名称": "策略库样例1", "策略类型": "策略库样例1",
            "触发条件": "策略库样例1", "执行动作": "策略库样例1", "生效周期": "策略库样例1",
            "策略评分": "策略库样例1", "使用次数": "策略库样例1",
        })
        realtime = self.client.get("/api/realtime").json()
        self.assertEqual(realtime["total"], 3)
        self.assertEqual(realtime["items"][2]["规则编号"], "REAL-0003")

    def test_detail_and_action_response_shape(self) -> None:
        detail = self.client.get("/api/realtime/3").json()
        self.assertEqual(detail["status"], "已生效")
        response = self.client.post(
            "/api/realtime/1/actions", json={"values": {"action": "生效规则"}}
        ).json()
        self.assertEqual(set(response), {"ok", "message", "entry"})
        self.assertTrue(response["ok"])
        self.assertEqual(response["entry"]["id"], 1)
        self.assertEqual(response["entry"]["status"], "已生效")
        self.assertEqual(self.client.get("/api/realtime/1").json()["status"], "已生效")

    def test_paging_filter_and_errors(self) -> None:
        paged = self.client.get("/api/realtime?page=2&size=2").json()
        self.assertEqual([row["规则编号"] for row in paged["items"]], ["REAL-0003"])
        self.assertEqual(paged["total"], 3)
        self.assertEqual(self.client.get("/api/realtime?keyword=ZZZ").json()["total"], 0)
        self.assertEqual(self.client.get("/api/realtime?size=201").status_code, 400)
        self.assertEqual(self.client.get("/api/realtime/999").status_code, 404)
        self.assertEqual(
            self.client.post("/api/realtime/1/actions", json={"values": {"action": "乱来"}}).json()["ok"],
            False,
        )

    def test_create_only_keeps_required_fields(self) -> None:
        response = self.client.post("/api/tacticlib", json={"values": {
            "策略编号": "TACT-9001", "策略名称": "新策略", "策略类型": "实时", "额外字段": "忽略",
        }}).json()
        self.assertEqual(set(response["entry"]), {
            "id", "status", "pending", "abnormal", "策略编号", "策略名称", "策略类型",
        })
        self.assertEqual(response["entry"]["status"], "草稿")

    def test_overview_module_count_unchanged(self) -> None:
        cards = self.client.get("/api/overview").json()["cards"]
        self.assertEqual(cards[0]["value"], 18)


class ArbitrationTest(unittest.TestCase):
    """动作冲突裁决：同一触发对象只保留一个最终动作。"""

    def setUp(self) -> None:
        _reset()

    def _register(self, sessions: list[dict]) -> dict:
        return ledger.register(sessions)

    def test_same_priority_uses_registration_basis_not_time(self) -> None:
        detail = self._register([
            {"触发对象": "对象-同优先级", "触发条件": "浏览未下单", "优先级": "5",
             "执行动作": "发券A", "来源": "策略库", "关联编号": "P-A",
             "会话开始时间": "2026-11-17T10:00:00+00:00"},
            {"触发对象": "对象-同优先级", "触发条件": "浏览未下单", "优先级": "5",
             "执行动作": "发券B", "来源": "实时决策", "关联编号": "P-B",
             "会话开始时间": "2026-11-17T09:00:00+00:00"},
        ])
        self.assertEqual(detail["最终执行动作"], "发券A")
        self.assertTrue(detail["裁决依据"].startswith("登记基准"))
        self.assertEqual(len(detail["落败会话"]), 1)
        self.assertIn("不参与裁决", detail["落败会话"][0]["落败原因"])

    def test_time_fields_do_not_change_verdict(self) -> None:
        """按会话开始时间或完成时间裁决会改变最终动作，这里保证两者都不参与裁决。"""
        base = [
            {"触发对象": "对象-时间", "优先级": "5", "执行动作": "发券A",
             "来源": "策略库", "关联编号": "T-A",
             "会话开始时间": "2026-11-17T10:00:00+00:00",
             "会话完成时间": "2026-11-17T10:09:00+00:00"},
            {"触发对象": "对象-时间", "优先级": "5", "执行动作": "发券B",
             "来源": "实时决策", "关联编号": "T-B",
             "会话开始时间": "2026-11-17T08:00:00+00:00",
             "会话完成时间": "2026-11-17T08:01:00+00:00"},
        ]
        first = self._register(copy.deepcopy(base))
        swapped = copy.deepcopy(base)
        swapped[0]["会话开始时间"], swapped[1]["会话开始时间"] = (
            swapped[1]["会话开始时间"], swapped[0]["会话开始时间"],
        )
        swapped[0]["会话完成时间"], swapped[1]["会话完成时间"] = (
            swapped[1]["会话完成时间"], swapped[0]["会话完成时间"],
        )
        second = self._register(swapped)
        self.assertEqual(first["最终执行动作"], "发券A")
        self.assertEqual(second["最终执行动作"], first["最终执行动作"])
        self.assertEqual(second["id"], first["id"])
        self.assertIn("仅记录", first["落败会话"][0]["落败原因"])

    def test_priority_beats_registration_order(self) -> None:
        self._register([{"触发对象": "对象-优先级", "优先级": "1", "执行动作": "低",
                         "来源": "策略库", "关联编号": "LOW"}])
        detail = self._register([{"触发对象": "对象-优先级", "优先级": "9", "执行动作": "高",
                                  "来源": "实时决策", "关联编号": "HIGH"}])
        self.assertEqual(detail["最终执行动作"], "高")
        self.assertEqual(len(detail["落败会话"]), 1)
        self.assertIn("优先级", detail["落败会话"][0]["落败原因"])

    def test_duplicate_registration_converges_to_one_detail(self) -> None:
        payload = [{"触发对象": "对象-重复", "优先级": "3", "执行动作": "动作1",
                    "来源": "策略库", "关联编号": "D-1"},
                   {"触发对象": "对象-重复", "优先级": "3", "执行动作": "动作2",
                    "来源": "实时决策", "关联编号": "D-2"}]
        first = self._register(payload)
        second = self._register(payload)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(ledger.list_details(keyword="对象-重复")[1], 1)

    def test_concurrent_same_priority_keeps_one_action(self) -> None:
        errors: list[str] = []

        def hit(index: int) -> None:
            try:
                ledger.register([{
                    "触发对象": "对象-并发", "优先级": "4", "执行动作": f"并发{index}",
                    "来源": "实时决策", "关联编号": f"C-{index}",
                }])
            except Exception as exc:  # pragma: no cover - 失败即测试失败
                errors.append(str(exc))

        threads = [threading.Thread(target=hit, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        items, total = ledger.list_details(keyword="对象-并发")
        self.assertEqual(total, 1)
        self.assertEqual(len(items[0]["落败会话"]), 3)
        winners = [s for s in ledger.sessions_of(items[0]["id"]) if s["状态"] == "生效"]
        self.assertEqual(len(winners), 1)

    def test_closed_detail_not_rewritten(self) -> None:
        detail = self._register([{"触发对象": "对象-已确认", "优先级": "7",
                                  "执行动作": "原动作", "来源": "策略库", "关联编号": "H-1"}])
        ledger.confirm(detail["id"])
        latest = self._register([{"触发对象": "对象-已确认", "优先级": "7",
                                  "执行动作": "新动作", "来源": "实时决策", "关联编号": "H-2"}])
        self.assertEqual(latest["最终执行动作"], "原动作")
        self.assertEqual(latest["id"], detail["id"])
        self.assertEqual(latest["落败会话"], [])
        self.assertTrue(latest["后续登记"])
        self.assertIn("已确认结果", latest["后续登记"][-1]["落败会话"][0]["落败原因"])

    def test_failed_registration_rolls_back(self) -> None:
        before, _ = ledger.list_details(size=200)
        with self.assertRaises(Exception):
            ledger.register([
                {"触发对象": "对象-回退", "执行动作": "动作1"},
                {"触发条件": "缺触发对象", "执行动作": "动作2"},
            ])
        after, _ = ledger.list_details(size=200)
        self.assertEqual(before, after)
        self.assertEqual(ledger.list_details(keyword="对象-回退")[1], 0)


class TraceabilityTest(unittest.TestCase):
    """发布记录、规则命中结果、最终动作与冲突原因可一起回放。"""

    def setUp(self) -> None:
        _reset()
        self.tacticlib = TacticlibService()
        self.realtime = RealtimeService()

    def test_baseline_matches_real_0003(self) -> None:
        rows, total = ledger.list_details(baseline=True)
        self.assertEqual(total, 1)
        baseline = rows[0]
        real = next(row for row in store.rows("realtime") if row["规则编号"] == "REAL-0003")
        self.assertEqual(baseline["最终执行动作"], real["执行动作"])
        self.assertEqual(baseline["触发条件"], real["决策条件"])
        self.assertEqual(baseline["状态"], "已闭环")

    def test_publish_and_activation_share_session(self) -> None:
        entry, _ = self.tacticlib.create_entry({
            "策略编号": "REAL-0002", "策略名称": "共用会话", "策略类型": "实时",
        })
        entry["执行动作"] = "共用动作"
        self.assertIsNotNone(self.tacticlib.run_action(entry["id"], "发布策略")[0])

        rows, total = ledger.list_details(ref="REAL-0002")
        self.assertEqual(total, 1)
        detail = rows[0]
        sessions = ledger.sessions_of(detail["id"])
        self.assertEqual(len(sessions), 1)
        session_code = sessions[0]["会话编号"]
        self.assertTrue(sessions[0]["发布记录"])
        self.assertTrue(any(hit["来源"] == "发布同步" for hit in sessions[0]["命中结果"]))

        self.assertIsNotNone(self.realtime.run_action(2, "生效规则")[0])
        sessions = ledger.sessions_of(detail["id"])
        self.assertEqual([s["会话编号"] for s in sessions], [session_code])
        self.assertTrue(any(hit["来源"] == "规则生效" for hit in sessions[0]["命中结果"]))

        versions = {row["数据版本"] for row in ledger.versions()}
        detail = ledger.get_detail(detail["id"])
        self.assertIn(detail["发布记录"][0]["数据版本"], versions)
        self.assertTrue(all(hit["数据版本"] in versions for hit in detail["命中结果"]))
        self.assertTrue(all(code in versions for code in detail["会话数据版本"].values()))

    def test_explicit_link_shares_session_across_codes(self) -> None:
        entry, _ = self.tacticlib.create_entry({
            "策略编号": "TACT-ALIAS", "策略名称": "别名共用", "策略类型": "实时",
        })
        entry["关联规则"] = "REAL-0003"
        entry["触发条件"] = "别名触发"
        entry["执行动作"] = "别名动作"
        self.assertIsNotNone(self.tacticlib.run_action(entry["id"], "发布策略")[0])

        rows, _ = ledger.list_details(ref="REAL-0003")
        detail = next(row for row in rows if not row["是否核对基准"])
        self.assertLessEqual({"TACT-ALIAS", "REAL-0003"}, set(detail["关联编号"]))
        sessions = ledger.sessions_of(detail["id"])
        self.assertEqual(len(sessions), 1)
        real = next(row for row in store.rows("realtime") if row["规则编号"] == "REAL-0003")
        self.assertEqual(detail["最终执行动作"], real["执行动作"])

    def test_publish_writeback_rolls_back_with_failure(self) -> None:
        entry, _ = self.tacticlib.create_entry({
            "策略编号": "TACT-RB", "策略名称": "回退检查", "策略类型": "实时",
        })
        entry["关联规则"] = "REAL-0002"
        entry["执行动作"] = "回退动作"
        before = copy.deepcopy(
            next(row for row in store.rows("realtime") if row["规则编号"] == "REAL-0002")
        )

        original = ledger.ensure_detail

        def boom(*args, **kwargs):
            raise RuntimeError("模拟登记失败")

        ledger.ensure_detail = boom
        try:
            result, message = self.tacticlib.run_action(entry["id"], "发布策略")
        finally:
            ledger.ensure_detail = original

        self.assertIsNone(result)
        self.assertIn("回退", message)
        self.assertEqual(self.tacticlib.get_entry(entry["id"])["status"], "草稿")
        after = next(row for row in store.rows("realtime") if row["规则编号"] == "REAL-0002")
        self.assertEqual(after, before)
        self.assertEqual(ledger.publish_records(entry), [])

    def test_bad_link_rejected_without_residue(self) -> None:
        entry, _ = self.tacticlib.create_entry({
            "策略编号": "TACT-BAD", "策略名称": "坏关联", "策略类型": "实时",
        })
        entry["关联规则"] = "REAL-9999"
        before = len(ledger.versions())
        result, message = self.tacticlib.run_action(entry["id"], "发布策略")
        self.assertIsNone(result)
        self.assertIn("关联规则", message)
        self.assertEqual(len(ledger.versions()), before)
        self.assertEqual(self.tacticlib.get_entry(entry["id"])["status"], "草稿")


if __name__ == "__main__":
    unittest.main()
