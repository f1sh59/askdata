from __future__ import annotations

import unittest
from typing import ClassVar

from askdata.clarify import (
    ClarifyDecision,
    Slot,
    VagueQuestion,
    covered_slots,
    evaluate_vague,
    parse_decision,
)


class ParseDecisionTest(unittest.TestCase):
    def test_plain_json(self) -> None:
        decision = parse_decision('{"need_clarification": true, "question": "哪个时间段？"}')
        self.assertTrue(decision.need_clarification)
        self.assertEqual(decision.question, "哪个时间段？")

    def test_fenced_json(self) -> None:
        text = '好的：\n```json\n{"need_clarification": false, "sql": "SELECT 1"}\n```'
        decision = parse_decision(text)
        self.assertFalse(decision.need_clarification)
        self.assertEqual(decision.sql, "SELECT 1")

    def test_text_around_json(self) -> None:
        text = '我先判断一下 {"need_clarification": false, "sql": "SELECT 2"} 就这样'
        decision = parse_decision(text)
        self.assertEqual(decision.sql, "SELECT 2")

    def test_missing_json_is_reported(self) -> None:
        decision = parse_decision("抱歉，我无法回答")
        self.assertFalse(decision.need_clarification)
        self.assertIn("没有 JSON", decision.parse_error or "")

    def test_broken_json_is_reported(self) -> None:
        decision = parse_decision('{"need_clarification": tru}')
        self.assertIsNotNone(decision.parse_error)

    def test_missing_fields_default_to_empty(self) -> None:
        decision = parse_decision('{"need_clarification": false}')
        self.assertEqual(decision.sql, "")
        self.assertEqual(decision.question, "")


def item(*slots: Slot) -> VagueQuestion:
    return VagueQuestion(id="v01", question="最近销售额怎么样？", why="缺时间", slots=list(slots))


TIME = Slot("time_range", ("时间", "哪年", "范围"))
METRIC = Slot("metric", ("指标", "销售额", "销量"))


class SlotCoverageTest(unittest.TestCase):
    def test_all_slots_covered(self) -> None:
        covered = covered_slots(item(TIME, METRIC), "请告诉我时间范围，以及要看哪个指标？")
        self.assertEqual(covered, ["time_range", "metric"])

    def test_partial_coverage(self) -> None:
        covered = covered_slots(item(TIME, METRIC), "请问时间范围是什么？")
        self.assertEqual(covered, ["time_range"])

    def test_no_coverage(self) -> None:
        self.assertEqual(covered_slots(item(TIME), "好的，马上查"), [])


class StatusTest(unittest.TestCase):
    def build(self, need: bool, question: str) -> object:
        from askdata.clarify import VagueResult

        return VagueResult(
            item=item(TIME, METRIC),
            decision=ClarifyDecision(need_clarification=need, question=question),
            covered=covered_slots(item(TIME, METRIC), question) if need else [],
        )

    def test_good(self) -> None:
        self.assertEqual(self.build(True, "时间范围？哪个指标？").status, "good")

    def test_partial(self) -> None:
        self.assertEqual(self.build(True, "时间范围是什么？").status, "partial")

    def test_irrelevant(self) -> None:
        self.assertEqual(self.build(True, "好的马上查").status, "irrelevant")

    def test_guessed(self) -> None:
        self.assertEqual(self.build(False, "").status, "guessed")


class EvaluateTest(unittest.TestCase):
    def test_evaluate_collects_results(self) -> None:
        class FakeStrategy:
            def decide(self, text: str) -> ClarifyDecision:
                return ClarifyDecision(need_clarification=True, question="时间范围？指标？")

        results = evaluate_vague(FakeStrategy(), [item(TIME, METRIC)])  # type: ignore[arg-type]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "good")
        self.assertEqual(results[0].coverage, 1.0)


class FakeLLM:
    """按顺序吐出预设回复，用来测闭环的两轮逻辑。"""

    def __init__(self, replies: list[str]) -> None:
        self.model = "fake"
        self.usage = {"calls": 0}
        self.replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, prompt: str):
        self.prompts.append(prompt)
        from types import SimpleNamespace

        return SimpleNamespace(content=self.replies.pop(0))


class ClosedLoopTest(unittest.TestCase):
    def make_item(self):
        from askdata.clarify import ClosedLoopItem

        return ClosedLoopItem(id="v01", question="最近销售额怎么样？", answer="2025 年全年。", gold_sql="SELECT 1")

    def strategy(self, replies: list[str]):
        from askdata.clarify import ClosedLoopStrategy

        llm = FakeLLM(replies)
        return ClosedLoopStrategy(llm, lambda text: "SCHEMA"), llm

    def test_second_turn_carries_question_clarification_and_answer(self) -> None:
        strategy, llm = self.strategy([
            '{"need_clarification": true, "question": "哪个时间段？"}',
            "```sql\nSELECT 2\n```",
        ])
        sql, decision = strategy.answer(self.make_item())

        self.assertEqual(sql, "SELECT 2")
        self.assertTrue(decision.need_clarification)
        self.assertEqual(len(llm.prompts), 2)
        self.assertIn("哪个时间段？", llm.prompts[1])
        self.assertIn("2025 年全年。", llm.prompts[1])

    def test_no_clarification_skips_second_turn(self) -> None:
        strategy, llm = self.strategy(['{"need_clarification": false, "sql": "SELECT 3"}'])
        sql, decision = strategy.answer(self.make_item())

        self.assertEqual(sql, "SELECT 3")
        self.assertFalse(decision.need_clarification)
        self.assertEqual(len(llm.prompts), 1)

    def test_run_closed_loop_counts_fixes(self) -> None:
        from askdata.clarify import ClosedLoopItem, run_closed_loop

        items = [
            ClosedLoopItem(id="a", question="q1", answer="a1", gold_sql="SELECT 1"),
            ClosedLoopItem(id="b", question="q2", answer="a2", gold_sql="SELECT 2"),
        ]

        class FakeSandbox:
            def run(self, sql: str):
                from askdata.db import QueryResult

                if sql == "SELECT 1":
                    return QueryResult(["c"], [(1,)])
                if sql == "SELECT 2":
                    return QueryResult(["c"], [(2,)])
                return QueryResult(["c"], [(99,)])

        class FakeClosed:
            def answer(self, item):
                # 第一题答对，第二题答错
                return ("SELECT 1" if item.id == "a" else "SELECT 99"), ClarifyDecision(True)

        class FakeBaseline:
            def generate(self, question):
                return "SELECT 99"

        results = run_closed_loop(FakeSandbox(), items, FakeClosed(), FakeBaseline())  # type: ignore[arg-type]
        self.assertEqual([r.closed_status for r in results], ["correct", "value_mismatch"])
        self.assertEqual([r.baseline_status for r in results], ["value_mismatch", "value_mismatch"])
        self.assertEqual(sum(1 for r in results if r.fixed), 1)

class ClarifyStrategyTest(unittest.TestCase):
    """策略层：把"模型怎么回"变成"判定是什么"。

    原文件测的是判定函数本身，这里补上游——模型回复 → parse → status 的整条链路。
    """

    def test_uses_stub_reply(self) -> None:
        from askdata.clarify import ClarifyStrategy
        from askdata.llm import LLMResponse

        class Stub:
            usage: ClassVar[dict] = {}

            def complete(self, prompt):
                return LLMResponse('{"need_clarification": true, "question": "哪一年？"}')

        strategy = ClarifyStrategy(Stub(), schema_provider=lambda _q: "schema")
        decision = strategy.decide("销售额是多少")
        self.assertTrue(decision.need_clarification)
        self.assertEqual(decision.question, "哪一年？")

    def test_llm_failure_is_reported_not_raised(self) -> None:
        from askdata.clarify import ClarifyStrategy

        class Boom:
            usage: ClassVar[dict] = {}

            def complete(self, prompt):
                return None

        strategy = ClarifyStrategy(Boom(), schema_provider=lambda _q: "schema")
        decision = strategy.decide("销售额是多少")
        self.assertFalse(decision.need_clarification)
        self.assertIn("调用失败", decision.parse_error or "")


class StatusMatrixTest(unittest.TestCase):
    """一次跑完五种判定，钉住"追问率怎么算"这件事。"""

    def test_five_statuses(self) -> None:
        from askdata.llm import LLMResponse

        class Stub:
            usage: ClassVar[dict] = {}

            def __init__(self, replies: list[str]) -> None:
                self.replies = list(replies)

            def complete(self, prompt):
                return LLMResponse(self.replies.pop(0))

        def item(*slots):
            return VagueQuestion(
                id="v", question="销售额是多少", why="缺条件",
                slots=[Slot(name, keywords) for name, keywords in slots],
            )

        time_slot = ("年份", ("年",))
        channel_slot = ("渠道", ("渠道",))
        items = [item(time_slot, channel_slot), item(time_slot, channel_slot),
                 item(time_slot), item(), item(time_slot)]
        replies = [
            '{"need_clarification": true, "question": "哪一年、哪个渠道？"}',
            '{"need_clarification": true, "question": "哪个渠道？"}',
            '{"need_clarification": true, "question": "能再具体点吗？"}',
            '{"need_clarification": true, "question": "补充一下？"}',
            '{"need_clarification": false, "sql": "SELECT 1"}',
        ]
        from askdata.clarify import ClarifyStrategy

        strategy = ClarifyStrategy(Stub(replies), schema_provider=lambda _q: "s")
        statuses = [r.status for r in evaluate_vague(strategy, items)]
        self.assertEqual(statuses, ["good", "partial", "irrelevant", "asked", "guessed"])


class LoadVagueTest(unittest.TestCase):
    def test_loads_fixture_file(self) -> None:
        import json as _json
        import tempfile
        from pathlib import Path as _Path

        from askdata.clarify import load_vague

        payload = {
            "version": "v",
            "questions": [
                {"id": "v1", "question": "销售额是多少", "why": "缺时间",
                 "slots": [{"name": "时间", "keywords": ["年", "月"]}]}
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = _Path(tmp) / "vague.json"
            path.write_text(_json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            items = load_vague(path)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].slots[0].name, "时间")


if __name__ == "__main__":
    unittest.main()
