"""鉴权与行级权限开关。

这个服务默认**不鉴权**（内网演示方便），但企业场景必须能开。
所以做成环境变量开关，默认关闭、开了就全程生效：

    ASKDATA_TOKEN=某个足够长的随机串    # 开启鉴权，/api/* 都要带 Bearer token
    ASKDATA_ROW_POLICY=1                # 所有查询都套行级权限（只看得到部分渠道）

设计上刻意简单：**一个共享 token，没有用户体系**。
真实的生产系统需要用户、角色、审计——这里只做到"能证明这条链路存在"。
写在文档里的限制，比假装做全了更负责。
"""

from __future__ import annotations

import os
import secrets


def expected_token() -> str | None:
    """配置里的 token；没配就是 None（= 不鉴权）。"""
    token = (os.environ.get("ASKDATA_TOKEN") or "").strip()
    return token or None


def bearer_of(header_value: str | None) -> str | None:
    """从 `Authorization: Bearer xxx` 里取出 xxx。"""
    if not header_value:
        return None
    parts = header_value.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def authorized(header_value: str | None, expected: str | None = None) -> bool:
    """校验请求头。没配 token 时一律放行。"""
    token = expected if expected is not None else expected_token()
    if not token:
        return True
    provided = bearer_of(header_value)
    if not provided:
        return False
    # 定长比较，避免按字节比较泄露长度信息。
    # 必须先编码成 bytes：compare_digest 不接受含非 ASCII 的 str，
    # 否则客户端发一个 `Bearer 中文` 就能让服务抛 TypeError（500 而不是 401）。
    return secrets.compare_digest(provided.encode("utf-8"), token.encode("utf-8"))


def row_policy_enabled() -> bool:
    """是否强制所有查询套行级权限。"""
    return (os.environ.get("ASKDATA_ROW_POLICY") or "").strip().lower() in {"1", "true", "yes", "on"}

def configured_api_key() -> str | None:
    """服务端是否已经配好模型密钥（.env 或环境变量）。"""
    key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    return key or None
