"""报告渲染与多轮聚合：这两块是"给人看的结果"，写错没人会发现。

评测报告和 CLI 输出是这个项目对外的脸面，但它们没有副作用、
不依赖数据库，是最容易补测的部分——之前一直是 0 覆盖，纯粹是没写。
"""

from __future__ import annotations

import unittest

from askdata.db import QueryResult
from askdata.models import Question
from askdata.multiturn import MultiTurnReport, PairResult
from askdata.report import render_markdown, to_dict
from askdata.runner import EvalResult, QuestionResult
from askdata.scoring import CORRECT, SQL_ERROR, Outcome


def _qr(rows: list[tuple]) -> QueryResult:
    return QueryResult(columns=["c"], rows=rows)


def _result(
    passed: bool, question: str = "各渠道销售额", sql: str = "SELECT 1", qid: str = "q1"
) -> QuestionResult:
    """按 runner.QuestionResult 的真实结构造一条评测结果。"""
    status = CORRECT if passed else SQL_ERROR
    return QuestionResult(
        question=Question(
            id=qid, difficulty="easy", tags=(), question=question, gold_sql=sql
        ),
        generated_sql=sql if passed else "SELECT bad",
        outcome=Outcome(status=status, gold_row_count=1, pred_row_count=1),
        gold=_qr([(1,)]),
        pred=_qr([(1,)]) if passed else _qr([]),
        attempt_count=1,
    )


class ReportMarkdownTest(unittest.TestCase):
    def test_contains_headline_numbers(self) -> None:
        results = [_result(True), _result(True), _result(False, qid="q3"), _result(False, qid="q4")]
        text = render_markdown(EvalResult(strategy_name="llm", dataset_version="v2", started_at="t", results=results))

        self.assertIn("50.0%", text)  # 2/4
        self.assertIn("4", text)

    def test_lists_failures(self) -> None:
        text = render_markdown(EvalResult(strategy_name="s", dataset_version="v2", started_at="t",
                                          results=[_result(True), _result(False, question="失败的问题", qid="q2")]))
        self.assertIn("失败的问题", text)

    def test_empty_results_does_not_crash(self) -> None:
        text = render_markdown(EvalResult(strategy_name="s", dataset_version="v2", started_at="t"))
        self.assertIsInstance(text, str)
        self.assertTrue(text)

    def test_markdown_has_table_header(self) -> None:
        text = render_markdown(EvalResult(strategy_name="s", dataset_version="v2", started_at="t", results=[_result(True)]))
        self.assertIn("|", text)
        self.assertIn("---", text)


class ReportDictTest(unittest.TestCase):
    def test_dict_shape(self) -> None:
        payload = to_dict(EvalResult(strategy_name="s", dataset_version="v2", started_at="t",
                                     results=[_result(True), _result(False)]))
        self.assertIsInstance(payload, dict)
        self.assertIn("execution_accuracy", payload)
        self.assertAlmostEqual(payload["execution_accuracy"], 0.5, places=3)

    def test_dict_is_json_serializable(self) -> None:
        import json

        payload = to_dict(EvalResult(strategy_name="s", dataset_version="v2", started_at="t", results=[_result(True)]))
        json.dumps(payload)  # 不抛异常即通过


def _pair(pair_id: str, kind: str, status: str, rewritten: str | None = "改写过") -> PairResult:
    return PairResult(
        pair_id=pair_id,
        kind=kind,
        followup="那 2024 年呢？",
        rewritten=rewritten,
        sql="SELECT 1",
        outcome=Outcome(status=status, gold_row_count=1, pred_row_count=1 if status == CORRECT else 0),
        gold_rows=1,
        pred_rows=1 if status == CORRECT else 0,
    )


class MultiTurnAggregateTest(unittest.TestCase):
    def _report(self) -> MultiTurnReport:
        # 单轮（对照组）：全错；多轮（实验组）：全对 —— 这正是 P9 想证明的差异
        control = [_pair("p1", "省略时间范围", SQL_ERROR), _pair("p2", "省略时间范围", "wrong_value")]
        experiment = [_pair("p1", "省略时间范围", CORRECT), _pair("p2", "指代替换", CORRECT)]
        return MultiTurnReport(
            dataset_version="mt-v1",
            started_at="2026-01-01T00:00:00",
            repeats=3,
            control=control,
            multiturn=[experiment, experiment, experiment],
        )

    def test_control_rate_is_zero(self) -> None:
        report = self._report()
        self.assertEqual(report.control_rate["ex"], 0.0)

    def test_multiturn_rate_is_one(self) -> None:
        report = self._report()
        self.assertAlmostEqual(report.mean_ex, 1.0, places=3)

    def test_by_kind_groups_and_sorts(self) -> None:
        rows = self._report().by_kind()
        self.assertTrue(rows)
        kinds = [r[0] for r in rows]
        self.assertIn("省略时间范围", kinds)
        # 按通过率倒序
        rates = [r[3] for r in rows]
        self.assertEqual(rates, sorted(rates, reverse=True))

    def test_as_dict_serializable(self) -> None:
        import json

        json.dumps(self._report().as_dict())

    def test_render_markdown_mentions_delta(self) -> None:
        from askdata.multiturn import render_markdown as mt_markdown

        text = mt_markdown(self._report())
        self.assertIn("多轮", text)


class CliSmokeTest(unittest.TestCase):
    """CLI 只做冒烟：解析不炸、未知命令有明确退出码。"""

    def test_no_args_lists_commands(self) -> None:
        """没有子命令时 argparse 会打印用法并退出——这里验证用法里列了子命令。"""
        import contextlib
        import io

        from askdata.cli import main

        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer), contextlib.suppress(SystemExit):
            main([])
        self.assertIn("validate", buffer.getvalue())

    def test_unknown_command_exits_nonzero(self) -> None:
        from askdata.cli import main

        with self.assertRaises(SystemExit) as ctx:
            main(["no-such-command"])
        self.assertNotEqual(ctx.exception.code, 0)


class ReportBranchesTest(unittest.TestCase):
    """render_markdown 的分支：失败明细、按难度/标签汇总、额外字段。

    这些分支只在"有失败"或"有额外统计"时才走到，之前没测到——
    而失败明细恰恰是报告里最该被看到的部分。
    """

    def _mixed_results(self):
        from askdata.db import QueryResult
        from askdata.models import Question
        from askdata.runner import QuestionResult
        from askdata.scoring import CORRECT, EMPTY_RESULT, NO_SQL, SQL_ERROR, Outcome

        def make(qid: str, status: str, difficulty: str = "medium", tag: str = "join"):
            return QuestionResult(
                question=Question(
                    id=qid, difficulty=difficulty, tags=(tag,), question=f"问题{qid}",
                    gold_sql="SELECT 1",
                ),
                generated_sql="SELECT 1",
                outcome=Outcome(status=status, gold_row_count=1, pred_row_count=1),
                gold=QueryResult(columns=["c"], rows=[(1,)]),
                pred=QueryResult(columns=["c"], rows=[(1,)]),
            )

        return [
            make("q1", CORRECT),
            make("q2", SQL_ERROR, difficulty="hard", tag="time"),
            make("q3", EMPTY_RESULT, difficulty="hard", tag="time"),
            make("q4", NO_SQL, difficulty="easy", tag="agg"),
            make("q5", "wrong_value", difficulty="easy", tag="agg"),
        ]

    def test_failures_are_listed(self) -> None:
        from askdata.report import render_markdown
        from askdata.runner import EvalResult

        result = EvalResult(
            strategy_name="llm", dataset_version="v2", started_at="t", results=self._mixed_results()
        )
        text = render_markdown(result)
        # 失败题的题号应该出现在明细里
        for qid in ("q2", "q3", "q4", "q5"):
            self.assertIn(qid.replace("q", ""), text)

    def test_by_difficulty_section(self) -> None:
        from askdata.report import render_markdown
        from askdata.runner import EvalResult

        result = EvalResult(
            strategy_name="llm", dataset_version="v2", started_at="t", results=self._mixed_results()
        )
        text = render_markdown(result)
        self.assertIn("hard", text)
        self.assertIn("easy", text)

    def test_extra_fields_rendered(self) -> None:
        from askdata.report import render_markdown
        from askdata.runner import EvalResult

        result = EvalResult(
            strategy_name="llm", dataset_version="v2", started_at="t",
            results=self._mixed_results(), extra={"prompt_tokens": 1234, "api_errors": 2},
        )
        text = render_markdown(result)
        self.assertIn("prompt tokens", text)

    def test_to_dict_has_failure_detail(self) -> None:
        from askdata.report import to_dict
        from askdata.runner import EvalResult

        result = EvalResult(
            strategy_name="llm", dataset_version="v2", started_at="t", results=self._mixed_results()
        )
        payload = to_dict(result)
        self.assertIn("results", payload)
        self.assertEqual(len(payload["results"]), 5)
        statuses = {r["status"] for r in payload["results"]}
        self.assertIn("sql_error", statuses)


if __name__ == "__main__":
    unittest.main()
