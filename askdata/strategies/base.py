"""策略接口：任何"问题 → SQL"的实现都实现这个协议。

这样 P0 的模板 baseline、P1 的单轮 LLM、P2 的 Schema Linking 都能用同一个 runner 打分，
指标才可横向比较。
"""

from __future__ import annotations

from typing import Protocol

from ..models import Question


class Strategy(Protocol):
    name: str

    def generate(self, question: Question) -> str | None:
        """返回 SQL；返回 None 表示该策略选择不回答（会计入 no_sql）。"""
        ...
