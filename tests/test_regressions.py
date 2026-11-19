"""回归测试清单：每个用例对应一个真实踩过的坑。

和普通单测的区别：
    这里不测"功能对不对"，测"这个坑不能重现"。
    每个类的 docstring 都写清楚当时的症状，将来有人改坏了一眼能看懂。

为什么单独放一个文件：
    散在各处的测试容易被当成"随便写的"，出问题时也没人知道它是防什么的。
    集中成清单后，它同时是**缺陷档案**。
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from askdata.experiment import Experiment, render_markdown

SCHEMA = Path(__file__).resolve().parent.parent / "data" / "schema.sql"


def build_db(path: Path) -> None:
    import duckdb

    conn = duckdb.connect(str(path))
    for stmt in SCHEMA.read_text(encoding="utf-8").split(";"):
        if stmt.strip():
            conn.execute(stmt)
    conn.close()


def run_cli(argv: list[str]) -> tuple[int, str]:
    from askdata.cli import main

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(argv)
    return code, buffer.getvalue()


class Bug01MultiturnIgnoresDbTest(unittest.TestCase):
    """BUG-01：`multiturn` 命令忽略 --db，永远打开默认库。

    症状：
        `askdata --db /tmp/x.duckdb multiturn ...` 依然去开 data/askdata.duckdb，
        而默认库常被 web 服务占着，于是报「无法打开文件，另一个程序正在使用」，
        和你指定的路径毫无关系。

    根因：
        `pipeline = Pipeline()` 没传 db_path。其他命令都从 args.db 取。

    防回归：
        用临时库跑 multiturn，只要它去碰默认库就会打开失败（CI 里默认库不存在）。
    """

    def test_multiturn_uses_the_db_from_arguments(self) -> None:
        from unittest.mock import patch

        from askdata.llm import LLMResponse

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            db = base / "bug01.duckdb"
            build_db(db)

            pairs = base / "pairs.json"
            pairs.write_text(
                json.dumps(
                    {
                        "version": "v",
                        "pairs": [
                            {
                                "id": "p1",
                                "kind": "省略时间范围",
                                "base_question": "2025 年销售额是多少",
                                "followup": "那 2024 年呢？",
                                "gold_sql": "SELECT COUNT(*) FROM orders",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            # 模型换成固定回复，只看命令有没有用对库
            def fake_chat(self, system, user, **kwargs):
                return LLMResponse('{"need_clarification": false, "sql": "SELECT 1"}')

            from askdata import config

            original_reports = config.REPORT_DIR
            config.REPORT_DIR = base / "reports"
            try:
                with patch("askdata.strategies.llm.DeepSeekClient.chat", fake_chat):
                    code, out = run_cli(
                        ["--db", str(db), "multiturn", "--pairs", str(pairs), "--limit", "1"]
                    )
            finally:
                config.REPORT_DIR = original_reports

        # 关键断言：没有因为"打不开默认库"而失败
        self.assertIn(code, (0, 1), out)
        self.assertNotIn("另一个程序正在使用", out)


class Bug02EmptyExperimentRenderTest(unittest.TestCase):
    """BUG-02：实验没有 run 时 render_markdown 直接 IndexError。

    症状：
        实验跑到一半被打断（或还没开始写 run），生成报告时抛
        `IndexError: list index out of range`，整个命令崩掉，
        前面跑出来的东西也白费。

    根因：
        `experiment.runs[0].total` 没判空。

    防回归：
        空实验必须能渲染出一份（内容为 0 的）报告。
    """

    def test_empty_experiment_renders(self) -> None:
        experiment = Experiment(strategy_name="llm", dataset_version="v1")
        text = render_markdown(experiment)
        self.assertIn("运行次数", text)
        self.assertIn("0 题", text)


class Bug03CliMissingFileTest(unittest.TestCase):
    """BUG-03：题库文件不存在时 CLI 抛 Traceback。

    症状：
        `askdata --questions nope.json validate` 打出一整页堆栈，
        退出码还是 Python 的默认值。用户看不出"是文件没找到"。

    根因：
        `main()` 直接 `return args.func(args)`，没接文件异常。

    防回归：
        返回退出码 2，且 stderr 里有一句人话。
    """

    def test_missing_questions_file_is_friendly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            db = base / "bug03.duckdb"
            build_db(db)
            missing = base / "nope.json"

            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = run_cli(
                    ["--db", str(db), "--questions", str(missing), "validate"]
                )[0]

        self.assertEqual(code, 2)
        self.assertIn("找不到文件", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())


if __name__ == "__main__":
    unittest.main()
