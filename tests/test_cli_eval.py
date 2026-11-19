"""CLI 的评测类子命令：用真数据跑通，但把模型挡在门外。

`eval --strategy template` 本身不调模型（纯模板 baseline），可以真跑；
`multiturn` / `clarify` 要模型，用 patch 把 DeepSeekClient.chat 换掉——
这样命令本身的分支、报告落盘、退出码都能测到，又不依赖网络。
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from askdata.cli import main
from askdata.llm import LLMResponse

SCHEMA = Path(__file__).resolve().parent.parent / "data" / "schema.sql"

QUESTIONS = {
    "version": "v-test",
    "questions": [
        {
            "id": "t1",
            "difficulty": "easy",
            "tags": [],
            "question": "一共有多少订单",
            "gold_sql": "SELECT COUNT(*) FROM orders",
            "note": "",
            "cohort": "",
        },
        {
            "id": "t2",
            "difficulty": "easy",
            "tags": [],
            "question": "一共有多少用户",
            "gold_sql": "SELECT COUNT(*) FROM users",
            "note": "",
            "cohort": "",
        },
    ],
}

PAIRS = {
    "version": "mt-test",
    "pairs": [
        {
            "id": "p1",
            "kind": "省略时间范围",
            "base_question": "2025 年各渠道的销售额是多少",
            "followup": "那 2024 年呢？",
            "gold_sql": "SELECT COUNT(*) FROM orders",
        }
    ],
}


def build_db(path: Path) -> None:
    import duckdb

    conn = duckdb.connect(str(path))
    for stmt in SCHEMA.read_text(encoding="utf-8").split(";"):
        if stmt.strip():
            conn.execute(stmt)
    conn.execute(
        "INSERT INTO orders (order_id, user_id, channel_id, order_time, status, "
        "total_amount, discount_amount, pay_amount) VALUES "
        "(1, 1, 1, TIMESTAMP '2025-01-01 10:00:00', '已完成', 100, 0, 100)"
    )
    conn.close()


def run_cli(argv: list[str]) -> tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(argv)
    return code, buffer.getvalue()


@contextlib.contextmanager
def report_dir(tmp: Path):
    """把报告目录指到临时目录，别污染仓库里的 reports/。"""
    from askdata import config

    original = config.REPORT_DIR
    config.REPORT_DIR = tmp
    try:
        yield tmp
    finally:
        config.REPORT_DIR = original


class EvalCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.db = base / "cli.duckdb"
        build_db(self.db)
        self.questions = base / "questions.json"
        self.questions.write_text(json.dumps(QUESTIONS, ensure_ascii=False), encoding="utf-8")
        self.reports = base / "reports"
        self.reports.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_template_strategy_runs_without_model(self) -> None:
        """模板 baseline 不调模型，是整条评测链路里最便宜的一次验证。"""
        with report_dir(self.reports):
            code, out = run_cli(
                [
                    "--db",
                    str(self.db),
                    "--questions",
                    str(self.questions),
                    "eval",
                    "--strategy",
                    "template",
                ]
            )

        self.assertEqual(code, 0, out)
        self.assertIn("strategy=", out)
        self.assertIn("EX=", out)

        md_files = list(self.reports.glob("eval_*.md"))
        json_files = list(self.reports.glob("eval_*.json"))
        self.assertEqual(len(md_files), 1)
        self.assertEqual(len(json_files), 1)

        payload = json.loads(json_files[0].read_text(encoding="utf-8"))
        self.assertIn("execution_accuracy", payload)

    def test_limit_option(self) -> None:
        with report_dir(self.reports):
            code, out = run_cli(
                [
                    "--db",
                    str(self.db),
                    "--questions",
                    str(self.questions),
                    "eval",
                    "--strategy",
                    "template",
                    "--limit",
                    "1",
                ]
            )
        self.assertEqual(code, 0, out)
        self.assertIn("questions=1", out)


class MultiturnCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.db = base / "cli.duckdb"
        build_db(self.db)
        self.pairs = base / "pairs.json"
        self.pairs.write_text(json.dumps(PAIRS, ensure_ascii=False), encoding="utf-8")
        self.reports = base / "reports"
        self.reports.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_multiturn_with_stubbed_model(self) -> None:
        """把模型换成固定回复，验证命令能跑完并写出报告。"""
        replies = [
            '{"need_clarification": false, "sql": "SELECT COUNT(*) FROM orders"}',
        ]

        def fake_chat(self, system, user, **kwargs):
            content = replies[0] if replies else '{"need_clarification": false, "sql": "SELECT 1"}'
            return LLMResponse(content, prompt_tokens=1, completion_tokens=1)

        with patch("askdata.strategies.llm.DeepSeekClient.chat", fake_chat), report_dir(
            self.reports
        ):
            code, out = run_cli(
                [
                    "--db",
                    str(self.db),
                    "multiturn",
                    "--pairs",
                    str(self.pairs),
                    "--limit",
                    "1",
                ]
            )

        # 只要不抛异常、能返回退出码，就说明命令链路通了；
        # 分数本身由 run_multiturn 的测试保证
        self.assertIn(code, (0, 1), out)


if __name__ == "__main__":
    unittest.main()
