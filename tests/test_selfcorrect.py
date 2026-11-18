from __future__ import annotations

import unittest
from types import SimpleNamespace

from askdata.db import QueryResult
from askdata.models import Question
from askdata.strategies.selfcorrect import SelfCorrectingStrategy

QUESTION = Question(
    id="t01",
    difficulty="easy",
    tags=("count",),
    question="有多少订单？",
    gold_sql="SELECT 1",
)


class FakeBase:
    """只实现 SelfCorrectingStrategy 用到的那几个方法，不碰网络。"""

    def __init__(self, replies: list[str]) -> None:
        self.model = "fake"
        self.usage = {"model": "fake", "calls": 0, "parse_failures": 0}
        self.replies = list(replies)
        self.prompts: list[str] = []

    def build_prompt(self, question: Question) -> str:
        return "BASE_PROMPT"

    def complete(self, prompt: str):
        self.prompts.append(prompt)
        self.usage["calls"] += 1
        content = self.replies.pop(0)
        return SimpleNamespace(content=content, cached=False, prompt_tokens=10, completion_tokens=5)


class FakeSandbox:
    def __init__(self, results: list[QueryResult]) -> None:
        self.results = list(results)
        self.executed: list[str] = []

    def run(self, sql: str) -> QueryResult:
        self.executed.append(sql)
        return self.results.pop(0)


def ok(rows: int = 1) -> QueryResult:
    return QueryResult(["c"], [(i,) for i in range(rows)])


def failed(message: str = "Binder Error: boom") -> QueryResult:
    return QueryResult([], [], message)


def empty() -> QueryResult:
    return QueryResult(["c"], [])


class SelfCorrectTest(unittest.TestCase):
    def test_execution_error_triggers_retry_and_returns_fixed_sql(self) -> None:
        base = FakeBase(["```sql\nSELECT broken\n```", "```sql\nSELECT 1 AS x\n```"])
        sandbox = FakeSandbox([failed(), ok()])

        strategy = SelfCorrectingStrategy(base, sandbox, max_attempts=3)  # type: ignore[arg-type]
        final = strategy.generate(QUESTION)

        self.assertEqual(final, "SELECT 1 AS x")
        self.assertEqual(strategy.attempts["t01"], ["SELECT broken", "SELECT 1 AS x"])
        self.assertEqual(len(sandbox.executed), 2)

    def test_error_message_is_fed_back_into_prompt(self) -> None:
        base = FakeBase(["```sql\nSELECT broken\n```", "```sql\nSELECT 1 AS x\n```"])
        sandbox = FakeSandbox([failed("column refund_time must appear in GROUP BY"), ok()])

        strategy = SelfCorrectingStrategy(base, sandbox, max_attempts=2)  # type: ignore[arg-type]
        strategy.generate(QUESTION)

        second_prompt = base.prompts[1]
        self.assertIn("column refund_time must appear in GROUP BY", second_prompt)
        self.assertIn("SELECT broken", second_prompt)
        self.assertIn("BASE_PROMPT", second_prompt)

    def test_empty_result_retries_by_default(self) -> None:
        base = FakeBase(["```sql\nSELECT a\n```", "```sql\nSELECT b\n```"])
        sandbox = FakeSandbox([empty(), ok()])

        strategy = SelfCorrectingStrategy(base, sandbox, max_attempts=3)  # type: ignore[arg-type]
        final = strategy.generate(QUESTION)

        self.assertEqual(final, "SELECT b")
        self.assertEqual(len(base.prompts), 2)

    def test_empty_result_does_not_retry_when_disabled(self) -> None:
        base = FakeBase(["```sql\nSELECT a\n```"])
        sandbox = FakeSandbox([empty()])

        strategy = SelfCorrectingStrategy(
            base, sandbox, max_attempts=3, retry_on_empty=False  # type: ignore[arg-type]
        )
        final = strategy.generate(QUESTION)

        self.assertEqual(final, "SELECT a")
        self.assertEqual(len(base.prompts), 1)

    def test_stops_at_max_attempts(self) -> None:
        base = FakeBase(["```sql\nSELECT 1\n```"] * 3)
        sandbox = FakeSandbox([failed(), failed(), failed()])

        strategy = SelfCorrectingStrategy(base, sandbox, max_attempts=3)  # type: ignore[arg-type]
        strategy.generate(QUESTION)

        self.assertEqual(strategy.attempts["t01"], ["SELECT 1"] * 3)
        self.assertEqual(len(base.prompts), 3)

    def test_unparseable_reply_stops_the_loop(self) -> None:
        base = FakeBase(["抱歉，我无法回答"])
        sandbox = FakeSandbox([])

        strategy = SelfCorrectingStrategy(base, sandbox, max_attempts=3)  # type: ignore[arg-type]
        self.assertIsNone(strategy.generate(QUESTION))
        self.assertEqual(strategy.attempts["t01"], [])


if __name__ == "__main__":
    unittest.main()
