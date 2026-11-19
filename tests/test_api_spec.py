"""OpenAPI 文档的自我检查。

文档最容易出的问题是"写着有、其实没有"或者"写着这样、其实是那样"。
这里用两条测试把它钉住：

1. **防漂移**：清单里的每个接口，服务端源码里必须真的出现这个路径；
2. **防悬空引用**：所有 $ref 必须能在 components/schemas 里找到。

第二条尤其重要——$ref 写错时 OpenAPI 校验器不会报错，
只会让渲染出来的文档显示 `{"$ref": "..."}`。
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from askdata.api_spec import ENDPOINTS, build_openapi

SERVER_SRC = (Path(__file__).resolve().parent.parent / "askdata" / "server.py").read_text(
    encoding="utf-8"
)


def _walk_refs(node, found: list[str]) -> None:
    if isinstance(node, dict):
        if "$ref" in node:
            found.append(node["$ref"])
        for value in node.values():
            _walk_refs(value, found)
    elif isinstance(node, list):
        for item in node:
            _walk_refs(item, found)


class SpecShapeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = build_openapi()

    def test_openapi_version(self) -> None:
        self.assertEqual(self.spec["openapi"], "3.1.0")

    def test_info_complete(self) -> None:
        info = self.spec["info"]
        for key in ("title", "version", "description"):
            self.assertTrue(info.get(key), f"info.{key} 不能为空")

    def test_paths_are_absolute(self) -> None:
        for path in self.spec["paths"]:
            self.assertTrue(path.startswith("/"), path)

    def test_every_operation_has_responses(self) -> None:
        for path, ops in self.spec["paths"].items():
            for method, op in ops.items():
                self.assertIn("responses", op, f"{method.upper()} {path}")
                self.assertTrue(op["responses"], f"{method.upper()} {path}")
                self.assertIn("summary", op, f"{method.upper()} {path}")

    def test_all_refs_resolve(self) -> None:
        refs: list[str] = []
        _walk_refs(self.spec, refs)
        self.assertTrue(refs, "文档里应该有 $ref")

        schemas = self.spec["components"]["schemas"]
        for ref in refs:
            name = ref.rsplit("/", 1)[-1]
            self.assertIn(name, schemas, f"悬空引用：{ref}")

    def test_json_serializable(self) -> None:
        """能序列化才叫"机器可读"——这行本身就是给接入方的保证。"""
        text = json.dumps(self.spec, ensure_ascii=False)
        self.assertGreater(len(text), 1000)
        json.loads(text)


class DriftGuardTest(unittest.TestCase):
    """清单与服务端源码必须一致。"""

    def test_every_endpoint_exists_in_server(self) -> None:
        for ep in ENDPOINTS:
            raw_path = ep.path.split("?", 1)[0]
            self.assertIn(
                f'"{raw_path}"',
                SERVER_SRC,
                f"文档里写了 {ep.method} {raw_path}，但服务端源码里找不到这个路径字符串",
            )

    def test_no_duplicate_endpoints(self) -> None:
        seen = set()
        for ep in ENDPOINTS:
            key = (ep.method, ep.path)
            self.assertNotIn(key, seen, f"重复登记：{key}")
            seen.add(key)

    def test_server_routes_are_documented(self) -> None:
        """反向检查：源码里出现的 /api/... 路径，清单里都应该有。"""
        documented = {ep.path.split("?", 1)[0] for ep in ENDPOINTS}
        found = set(re.findall(r'"(/api/[a-z0-9/_\-.]+)"', SERVER_SRC))
        missing = {p for p in found if p not in documented}
        self.assertFalse(missing, f"这些接口没有写进清单：{sorted(missing)}")


if __name__ == "__main__":
    unittest.main()
