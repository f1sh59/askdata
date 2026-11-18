from __future__ import annotations

import time
import unittest

from askdata.session import SessionStore


class SessionBasicsTest(unittest.TestCase):
    def test_empty_history_for_unknown_session(self) -> None:
        store = SessionStore()
        self.assertEqual(store.history("nope"), [])
        self.assertEqual(store.history(None), [])

    def test_append_and_history(self) -> None:
        store = SessionStore()
        store.append("s1", "各渠道销售额", "SELECT 1")
        history = store.history("s1")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["question"], "各渠道销售额")
        self.assertEqual(history[0]["sql"], "SELECT 1")

    def test_none_session_is_noop(self) -> None:
        store = SessionStore()
        store.append(None, "q", "s")  # 不该抛错
        self.assertEqual(store.stats()["sessions"], 0)

    def test_sessions_are_isolated(self) -> None:
        store = SessionStore()
        store.append("a", "问题A", "SQL-A")
        store.append("b", "问题B", "SQL-B")
        self.assertEqual(store.history("a")[0]["question"], "问题A")
        self.assertEqual(store.history("b")[0]["question"], "问题B")


class SessionLimitTest(unittest.TestCase):
    def test_turns_are_capped(self) -> None:
        store = SessionStore(max_turns=3)
        for i in range(6):
            store.append("s", f"q{i}", f"sql{i}")
        history = store.history("s")
        self.assertEqual(len(history), 3)
        # 保留的是最新的三轮
        self.assertEqual([h["question"] for h in history], ["q3", "q4", "q5"])

    def test_ttl_expires(self) -> None:
        store = SessionStore(ttl_seconds=0)
        store.append("s", "q", "sql")
        time.sleep(0.01)
        self.assertEqual(store.history("s"), [])

    def test_too_many_sessions_evicts_oldest(self) -> None:
        store = SessionStore(max_sessions=2)
        for i in range(4):
            store.append(f"s{i}", "q", "sql")
            time.sleep(0.002)
        self.assertEqual(store.stats()["sessions"], 2)
        self.assertEqual(store.history("s0"), [])
        self.assertTrue(store.history("s3"))


class SessionClearTest(unittest.TestCase):
    def test_clear_removes_history(self) -> None:
        store = SessionStore()
        store.append("s", "q", "sql")
        self.assertTrue(store.clear("s"))
        self.assertEqual(store.history("s"), [])

    def test_clear_unknown_returns_false(self) -> None:
        store = SessionStore()
        self.assertFalse(store.clear("nope"))

    def test_clear_none_is_safe(self) -> None:
        store = SessionStore()
        self.assertFalse(store.clear(None))

    def test_clear_does_not_affect_other_sessions(self) -> None:
        store = SessionStore()
        store.append("a", "qa", "sql-a")
        store.append("b", "qb", "sql-b")
        store.clear("a")
        self.assertEqual(store.history("a"), [])
        self.assertEqual(len(store.history("b")), 1)


class SessionIdTest(unittest.TestCase):
    def test_ids_are_unique_and_nonempty(self) -> None:
        ids = {SessionStore.new_id() for _ in range(50)}
        self.assertEqual(len(ids), 50)
        self.assertTrue(all(len(i) >= 8 for i in ids))


if __name__ == "__main__":
    unittest.main()
