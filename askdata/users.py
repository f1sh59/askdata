"""用户、令牌与行级权限。

之前只有「一个共享 token」，等于所有人共用一个身份——出问题无法追溯到人，
也没法让不同的人看到不同范围的数据。这里补上最小可用的用户体系：

    id        u_xxxxx
    name      显示名
    token     明文只在创建时返回一次，存的是 sha256
    role      admin / viewer
    channels  这个人能看到哪些渠道（空 = 不限制）

设计取舍：
- **不用 JWT**：单进程服务，存表查表最快，也没有密钥轮换问题。
- **令牌只存哈希**：库泄露也拿不到明文；比较用 `compare_digest`。
- **创建时返回一次明文**：之后永远查不到——和 GitHub PAT 一样的规矩。

兼容老配置：只设置 `ASKDATA_TOKEN` 而不建用户时，行为与以前完全一致
（那个 token 被视为 admin）。
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROLES = ("admin", "viewer")


@dataclass(slots=True)
class User:
    id: str
    name: str
    token_hash: str
    role: str = "viewer"
    channels: tuple[str, ...] = ()
    created_at: float = field(default_factory=time.time)
    disabled: bool = False

    def as_public(self) -> dict[str, Any]:
        """给接口返回用：**永远不含 token 与 hash**。"""
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "channels": list(self.channels),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.created_at)),
            "disabled": self.disabled,
        }

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_id() -> str:
    return "u_" + secrets.token_hex(4)


class UserStore:
    """用户表：一个 JSON 文件，读写都加锁。

    单进程 + 低频写入，用 JSON 足够；换成数据库只是把这几个方法重写一遍。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._users: dict[str, User] = {}
        self._by_hash: dict[str, str] = {}
        self._load()

    # ---- 持久化 ----

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # 文件坏了不能让服务起不来：当成空表，等下一次写入覆盖
            return
        for item in raw.get("users", []):
            user = User(
                id=item["id"],
                name=item["name"],
                token_hash=item["token_hash"],
                role=item.get("role", "viewer"),
                channels=tuple(item.get("channels", ())),
                created_at=item.get("created_at", 0.0),
                disabled=bool(item.get("disabled", False)),
            )
            self._users[user.id] = user
            self._by_hash[user.token_hash] = user.id

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "users": [
                {
                    "id": u.id,
                    "name": u.name,
                    "token_hash": u.token_hash,
                    "role": u.role,
                    "channels": list(u.channels),
                    "created_at": u.created_at,
                    "disabled": u.disabled,
                }
                for u in self._users.values()
            ]
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)  # 原子替换：写一半断电也不会留下半个文件

    # ---- 查询 ----

    def __len__(self) -> int:
        return len(self._users)

    def list(self) -> list[dict[str, Any]]:
        return [u.as_public() for u in self._users.values()]

    def get(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    def authenticate(self, token: str | None) -> User | None:
        """按明文 token 找人。找不到或已禁用都返回 None。"""
        if not token:
            return None
        digest = hash_token(token)
        user_id = self._by_hash.get(digest)
        if not user_id:
            return None
        user = self._users.get(user_id)
        if user is None or user.disabled:
            return None
        return user

    # ---- 变更 ----

    def create(
        self,
        name: str,
        role: str = "viewer",
        channels: tuple[str, ...] = (),
        token: str | None = None,
    ) -> tuple[User, str]:
        """建用户，返回（用户, 明文 token）。明文只在这里出现一次。"""
        clean = (name or "").strip()
        if not clean:
            raise ValueError("用户名字不能为空")
        if role not in ROLES:
            raise ValueError(f"role 只能是 {' / '.join(ROLES)}")

        plain = token or ("ad_" + secrets.token_urlsafe(24))
        digest = hash_token(plain)
        if digest in self._by_hash:
            raise ValueError("这个 token 已经被使用了")

        user = User(
            id=_new_id(),
            name=clean,
            token_hash=digest,
            role=role,
            channels=tuple(channels),
        )
        self._users[user.id] = user
        self._by_hash[digest] = user.id
        self._save()
        return user, plain

    def delete(self, user_id: str) -> bool:
        user = self._users.pop(user_id, None)
        if user is None:
            return False
        self._by_hash.pop(user.token_hash, None)
        self._save()
        return True

    def disable(self, user_id: str, disabled: bool = True) -> bool:
        user = self._users.get(user_id)
        if user is None:
            return False
        user.disabled = disabled
        self._save()
        return True
