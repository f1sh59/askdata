"""鉴权：纯逻辑单测 + 一组起服务验证 401/放行的集成测试。"""

from __future__ import annotations

import importlib
import os
import unittest

from askdata import auth


class BearerTest(unittest.TestCase):
    def test_parses_bearer(self) -> None:
        self.assertEqual(auth.bearer_of("Bearer abc123"), "abc123")
        self.assertEqual(auth.bearer_of("bearer  abc123 "), "abc123")

    def test_rejects_other_schemes(self) -> None:
        self.assertIsNone(auth.bearer_of("Basic abc123"))
        self.assertIsNone(auth.bearer_of("abc123"))
        self.assertIsNone(auth.bearer_of(""))
        self.assertIsNone(auth.bearer_of(None))


class AuthorizedTest(unittest.TestCase):
    def test_no_token_configured_allows_everything(self) -> None:
        self.assertTrue(auth.authorized(None, expected=None))
        self.assertTrue(auth.authorized("Bearer whatever", expected=None))

    def test_missing_header_is_rejected(self) -> None:
        self.assertFalse(auth.authorized(None, expected="s3cret"))

    def test_wrong_token_is_rejected(self) -> None:
        self.assertFalse(auth.authorized("Bearer nope", expected="s3cret"))

    def test_correct_token_is_accepted(self) -> None:
        self.assertTrue(auth.authorized("Bearer s3cret", expected="s3cret"))

    def test_non_ascii_token_does_not_crash(self) -> None:
        # compare_digest 对非 ASCII 字符串会抛 TypeError，这里必须挡住
        self.assertFalse(auth.authorized("Bearer 中文", expected="s3cret"))

    def test_env_token_is_used(self) -> None:
        os.environ["ASKDATA_TOKEN"] = "env-token"
        try:
            self.assertTrue(auth.authorized("Bearer env-token"))
            self.assertFalse(auth.authorized("Bearer other"))
        finally:
            os.environ.pop("ASKDATA_TOKEN", None)

    def test_blank_env_means_no_auth(self) -> None:
        os.environ["ASKDATA_TOKEN"] = "   "
        try:
            self.assertIsNone(auth.expected_token())
            self.assertTrue(auth.authorized(None))
        finally:
            os.environ.pop("ASKDATA_TOKEN", None)


class RowPolicyTest(unittest.TestCase):
    def test_default_off(self) -> None:
        os.environ.pop("ASKDATA_ROW_POLICY", None)
        self.assertFalse(auth.row_policy_enabled())

    def test_truthy_values(self) -> None:
        for value in ("1", "true", "YES", "on"):
            os.environ["ASKDATA_ROW_POLICY"] = value
            self.assertTrue(auth.row_policy_enabled(), value)

    def test_falsy_values(self) -> None:
        for value in ("0", "false", ""):
            os.environ["ASKDATA_ROW_POLICY"] = value
            self.assertFalse(auth.row_policy_enabled(), value)
        os.environ.pop("ASKDATA_ROW_POLICY", None)


class GuardApiContractTest(unittest.TestCase):
    """钉住 SqlGuard 的接口形状。

    踩过的坑：行级权限是**构造时**传的（SqlGuard(policy=...)），
    check() 没有 policy 参数。当时写成 guard.check(sql, policy=True)，
    直到一次真实请求才炸出来——单测没覆盖到这条路径。
    """

    def test_policy_is_constructor_argument(self) -> None:
        import inspect

        from askdata.guard import SqlGuard

        self.assertIn("policy", inspect.signature(SqlGuard.__init__).parameters)
        self.assertNotIn("policy", inspect.signature(SqlGuard.check).parameters)

if __name__ == "__main__":
    unittest.main()
