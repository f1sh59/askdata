"""架构约束测试（fitness function）。

目录结构只是"看起来分层"，真正防止腐化的是**自动检查依赖方向**。
没有这层保护，半年后一定会有人在 db.py 里 import server 里的全局变量，
分层就名存实亡了。

这个项目定义四层：

    HTTP 层    server.py          只依赖 domain / infra / 通用件
    领域层      service.py 等       不依赖任何 HTTP 概念
    基础设施    db.py llm.py 等     不依赖领域层，也不依赖 HTTP
    工具层      observability/ratelimit/api_spec/stats  谁都能用，自己不依赖业务

允许的方向：HTTP → 领域 → 基础设施；反过来一律禁止。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "askdata"

HTTP_MODULES = {"server"}
DOMAIN_MODULES = {
    "service", "verify", "dialog", "clarify", "multiturn",
    "experiment", "scoring", "retrieval",
    # runner 是"评测编排"：它调用 scoring 和 strategies，属于领域层而不是工具层。
    # 这条是写测试时才发现分错的——它原本被当成叶子工具，实际在指挥业务。
    "runner", "report",
}
INFRA_MODULES = {"db", "llm", "guard", "imports", "schema"}
UTIL_MODULES = {"observability", "ratelimit", "api_spec", "stats", "config", "models", "session", "auth"}


def _imports_of(path: Path) -> set[str]:
    """取出一个文件里所有 `from .x import` / `import x` 指向的同包模块名。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # level>=1 表示相对导入，module 可能是 None（from . import x）
            if node.level and node.module:
                found.add(node.module.split(".")[0])
            elif node.level and not node.module:
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif node.module in {"http.server", "http"}:
                found.add("http")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in {"http", "urllib", "socket", "socketserver"}:
                    found.add(top)
    return found


def _all_modules() -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for path in PACKAGE.glob("*.py"):
        if path.name == "__init__.py":
            continue
        result[path.stem] = _imports_of(path)
    return result


class LayeringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = _all_modules()

    def test_domain_does_not_import_http_layer(self) -> None:
        """领域层不能反向依赖 HTTP：否则业务逻辑就跑不进 CLI 和测试。"""
        for module in DOMAIN_MODULES:
            if module not in self.graph:
                continue
            self.assertNotIn(
                "server",
                self.graph[module],
                f"{module}.py 依赖了 server.py——领域层不允许知道 HTTP 的存在",
            )

    def test_infra_does_not_import_domain(self) -> None:
        """基础设施不该依赖业务：db.py 只该懂 SQL，不该懂"问数"。"""
        for module in INFRA_MODULES:
            if module not in self.graph:
                continue
            for dep in self.graph[module]:
                self.assertNotIn(
                    dep,
                    DOMAIN_MODULES,
                    f"{module}.py 依赖了领域模块 {dep}.py——方向反了",
                )

    def test_infra_does_not_import_web_frameworks(self) -> None:
        """基础设施是纯的：不该把 http/urllib 之类的带进来（llm 除外，它要发请求）。"""
        for module in INFRA_MODULES - {"llm"}:
            if module not in self.graph:
                continue
            self.assertNotIn("http", self.graph[module], f"{module}.py 混进了 HTTP 依赖")

    def test_utility_modules_are_leaf(self) -> None:
        """工具层被所有人依赖，自己不该依赖业务模块。"""
        for module in UTIL_MODULES:
            if module not in self.graph:
                continue
            for dep in self.graph[module]:
                self.assertNotIn(
                    dep,
                    DOMAIN_MODULES | HTTP_MODULES,
                    f"工具模块 {module}.py 依赖了 {dep}.py，会形成环",
                )

    def test_no_module_imports_scripts_or_tests(self) -> None:
        """包不能反向依赖脚本或测试。"""
        for module, deps in self.graph.items():
            self.assertNotIn("scripts", deps, f"{module}.py 依赖了 scripts")
            self.assertNotIn("tests", deps, f"{module}.py 依赖了 tests")

    def test_http_layer_is_allowed_to_depend_on_domain(self) -> None:
        """正向检查：server.py 应该依赖 service，而不是自己重写业务。"""
        self.assertIn("service", self.graph.get("server", set()))


if __name__ == "__main__":
    unittest.main()
