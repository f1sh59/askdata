"""用户、令牌、行级权限、审计、会话持久化。

这几块是"能对外交付"和"只能内网演示"之间的分界线：
有用户才知道是谁在问，有审计才知道问过什么，会话落盘才不怕重启。
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from askdata.audit import AuditLog
from askdata.session import SessionStore
from askdata.users import UserStore, hash_token


class UserStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "users.json"
        self.store = UserStore(self.path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_create_returns_token_once(self) -> None:
        user, token = self.store.create("张三", role="admin")
        self.assertTrue(token.startswith("ad_"))
        self.assertEqual(user.role, "admin")
        # 明文不进公开结构
        self.assertNotIn("token", user.as_public())
        self.assertNotIn("token_hash", user.as_public())

    def test_authenticate_by_token(self) -> None:
        user, token = self.store.create("李四")
        found = self.store.authenticate(token)
        self.assertIsNotNone(found)
        self.assertEqual(found.id, user.id)

    def test_wrong_token_rejected(self) -> None:
        self.store.create("李四")
        self.assertIsNone(self.store.authenticate("ad_not-a-real-token"))
        self.assertIsNone(self.store.authenticate(None))

    def test_token_is_hashed_on_disk(self) -> None:
        """落盘不能有明文 token——库泄露也不能直接拿来登录。"""
        _, token = self.store.create("王五")
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn(token, raw)
        self.assertIn(hash_token(token), raw)

    def test_persisted_across_instances(self) -> None:
        _, token = self.store.create("赵六")
        reopened = UserStore(self.path)
        self.assertIsNotNone(reopened.authenticate(token))

    def test_delete_revokes_token(self) -> None:
        user, token = self.store.create("临时")
        self.assertTrue(self.store.delete(user.id))
        self.assertIsNone(self.store.authenticate(token))

    def test_disabled_user_cannot_auth(self) -> None:
        user, token = self.store.create("停用")
        self.store.disable(user.id, True)
        self.assertIsNone(self.store.authenticate(token))

    def test_duplicate_name_allowed_but_empty_rejected(self) -> None:
        """名字可以重，但要能区分（id 不同）；空名字不行。"""
        a, _ = self.store.create("同名")
        b, _ = self.store.create("同名")
        self.assertNotEqual(a.id, b.id)
        with self.assertRaises(ValueError):
            self.store.create("   ")

    def test_invalid_role_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.create("x", role="root")

    def test_channels_round_trip(self) -> None:
        user, token = self.store.create("渠道受限", channels=("抖音", "快手"))
        reopened = UserStore(self.path)
        found = reopened.authenticate(token)
        self.assertEqual(found.channels, ("抖音", "快手"))
        self.assertEqual(user.channels, ("抖音", "快手"))

    def test_broken_file_is_tolerated(self) -> None:
        self.path.write_text("{ 这不是 JSON", encoding="utf-8")
        store = UserStore(self.path)  # 不该抛
        self.assertEqual(len(store), 0)


class AuditLogTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "audit.jsonl"
        self.log = AuditLog(self.path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_record_and_read(self) -> None:
        self.log.record(user="张三", action="ask", question="销售额", sql="SELECT 1", row_count=3)
        entries = self.log.recent()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["user"], "张三")
        self.assertEqual(entries[0]["rows"], 3)

    def test_newest_first(self) -> None:
        self.log.record(user="a", action="ask")
        self.log.record(user="b", action="ask")
        self.assertEqual([e["user"] for e in self.log.recent()], ["b", "a"])

    def test_filter_by_user(self) -> None:
        self.log.record(user="a", action="ask")
        self.log.record(user="b", action="ask")
        self.assertEqual(len(self.log.recent(user="a")), 1)

    def test_limit(self) -> None:
        for i in range(10):
            self.log.record(user="x", action="ask", question=f"q{i}")
        self.assertEqual(len(self.log.recent(limit=3)), 3)

    def test_disabled_writes_nothing(self) -> None:
        log = AuditLog(self.path, enabled=False)
        log.record(user="a", action="ask")
        self.assertEqual(log.recent(), [])

    def test_long_question_truncated(self) -> None:
        self.log.record(user="a", action="ask", question="问" * 2000)
        self.assertLessEqual(len(self.log.recent()[0]["question"]), 500)

    def test_missing_file_is_empty(self) -> None:
        self.assertEqual(AuditLog(Path(self._tmp.name) / "nope.jsonl").recent(), [])


class PersistentSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "sessions.db"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_survives_restart(self) -> None:
        """这就是这次要解决的问题：重启服务后上下文还在。"""
        first = SessionStore(path=self.path)
        first.append("s1", "2025 年销售额", "SELECT 1")
        first.close()

        second = SessionStore(path=self.path)
        history = second.history("s1")
        second.close()

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["question"], "2025 年销售额")

    def test_clear_removes_from_disk(self) -> None:
        first = SessionStore(path=self.path)
        first.append("s1", "q", "SELECT 1")
        self.assertTrue(first.clear("s1"))
        first.close()

        second = SessionStore(path=self.path)
        self.assertEqual(second.history("s1"), [])
        second.close()

    def test_expired_sessions_not_restored(self) -> None:
        first = SessionStore(path=self.path, ttl_seconds=1)
        first.append("s1", "q", "SELECT 1")
        first.close()
        time.sleep(1.2)

        second = SessionStore(path=self.path, ttl_seconds=1)
        self.assertEqual(second.history("s1"), [])
        second.close()

    def test_memory_mode_still_works(self) -> None:
        store = SessionStore()  # path=None
        store.append("s1", "q", "SELECT 1")
        self.assertEqual(len(store.history("s1")), 1)
        self.assertFalse(store.stats()["persistent"])

    def test_stats_marks_persistent(self) -> None:
        store = SessionStore(path=self.path)
        self.assertTrue(store.stats()["persistent"])
        store.close()

    def test_max_turns_trimmed(self) -> None:
        store = SessionStore(path=self.path, max_turns=2)
        for i in range(5):
            store.append("s1", f"q{i}", "SELECT 1")
        self.assertEqual(len(store.history("s1")), 2)
        store.close()


class AuthModeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = {}

    def tearDown(self) -> None:
        import os

        from askdata import auth, config

        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        config.USERS_PATH = Path(self._tmp.name) / "users.json"
        auth.reset_store()
        self._tmp.cleanup()

    def _set(self, key: str, value: str) -> None:
        import os

        self._saved.setdefault(key, os.environ.get(key))
        os.environ[key] = value

    def test_open_when_nothing_configured(self) -> None:
        from askdata import auth, config

        self._set("ASKDATA_USERS", str(Path(self._tmp.name) / "empty.json"))
        self._saved.setdefault("ASKDATA_TOKEN", __import__("os").environ.get("ASKDATA_TOKEN"))
        __import__("os").environ.pop("ASKDATA_TOKEN", None)
        config.USERS_PATH = Path(self._tmp.name) / "empty.json"
        auth.reset_store()
        self.assertEqual(auth.auth_mode(), "open")
        self.assertIsNone(auth.current_user(None))

    def test_shared_token_is_admin(self) -> None:
        import os

        from askdata import auth, config

        self._set("ASKDATA_TOKEN", "shared-secret")
        config.USERS_PATH = Path(self._tmp.name) / "empty.json"
        auth.reset_store()
        self.assertEqual(auth.auth_mode(), "shared")
        user = auth.current_user("Bearer shared-secret")
        self.assertIsNotNone(user)
        self.assertTrue(auth.is_admin(user))
        self.assertIsNone(auth.current_user("Bearer wrong"))
        os.environ.pop("ASKDATA_TOKEN", None)

    def test_users_mode_uses_per_user(self) -> None:
        import os

        from askdata import auth, config

        self._set("ASKDATA_USERS", str(Path(self._tmp.name) / "users.json"))
        os.environ.pop("ASKDATA_TOKEN", None)
        config.USERS_PATH = Path(self._tmp.name) / "users.json"
        auth.reset_store()
        _, token = auth.store().create("张三", role="viewer")

        self.assertEqual(auth.auth_mode(), "users")
        user = auth.current_user(f"Bearer {token}")
        self.assertEqual(user.name, "张三")
        self.assertFalse(auth.is_admin(user))


if __name__ == "__main__":
    unittest.main()
