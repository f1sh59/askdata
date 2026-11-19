"""CLI 子命令：用临时库跑 validate / schema / attack 三条不依赖模型的命令。

CLI 是这个项目面向"想改代码的人"的入口，但它一直 0 覆盖——
因为大家默认"命令行嘛，手动跑跑就行"。实际上它对文件的读写、
退出码的约定，都值得钉住。
"""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from askdata.cli import main

SCHEMA = Path(__file__).resolve().parent.parent / "data" / "schema.sql"


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
    """跑一次 CLI，收集 stdout。"""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(argv)
    return code, buffer.getvalue()


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "cli.duckdb"
        build_db(self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_schema_prints_semantic_layer(self) -> None:
        code, out = run_cli(["--db", str(self.db), "schema"])
        self.assertEqual(code, 0)
        self.assertIn("orders", out)
        # 语义层是给模型看的，必须包含业务口径说明
        self.assertGreater(len(out), 500)

    def test_schema_writes_file(self) -> None:
        target = "tmp_cli_schema.txt"
        try:
            code, out = run_cli(["--db", str(self.db), "schema", "--out", target])
            self.assertEqual(code, 0)
            self.assertIn("written", out)
            written = Path(__file__).resolve().parent.parent / target
            self.assertTrue(written.exists())
            self.assertGreater(written.stat().st_size, 200)
        finally:
            Path(Path(__file__).resolve().parent.parent / "tmp_cli_schema.txt").unlink(
                missing_ok=True
            )

    def test_attack_blocks_all_injections(self) -> None:
        """拦截数是这条命令的核心结论，必须 50/50。

        "正常 SQL 会不会被误伤"依赖数据量：临时库只有 1 条订单，
        需要 join 的用例查不到行就会被算成误伤。所以只断言拦截侧。
        """
        code, out = run_cli(["--db", str(self.db), "attack"])
        self.assertIn("blocked 50/50", out, out)
        self.assertIn("allowed", out)
        self.assertIn(code, (0, 1))

    def test_validate_with_missing_dataset_fails_cleanly(self) -> None:
        """题库文件不存在时不能抛栈，要给出非零退出码。"""
        missing = str(Path(self._tmp.name) / "nope.json")
        try:
            code, _ = run_cli(["--db", str(self.db), "--questions", missing, "validate"])
            self.assertNotEqual(code, 0)
        except SystemExit as exc:  # argparse 层直接退出也算合理
            self.assertNotEqual(exc.code, 0)

    def test_unknown_command(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            main(["no-such-command"])
        self.assertNotEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
