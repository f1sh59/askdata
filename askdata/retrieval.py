"""表级召回：从几十张表里挑出"这道题可能需要的那几张"。

两路信号，刻意做得不一样：

* **metadata 路**：表名、表注释、字段名、字段注释 —— 回答"这张表是关于什么的"。
* **value 路**：枚举值（如 status 里的「已退款」）—— 回答"这张表里装的东西对不对得上"。

第二路是故意的：`refunds` 和 `fin_refund_audit` 在元数据上很像（都有 refund、都叫审核/记录），
但前者的数据里真的躺着「已退款」这个状态值，后者只是一张审核日志。只做元数据匹配分不出来。

融合用 RRF（k=60）——它只看排名不看分数，省掉了两路分数量纲不一致的麻烦。

说明：这里没有用真的向量检索。当前环境拿不到 embedding 服务（DeepSeek 不提供、pip 装不了本地模型），
所以两路都是字面信号。`TableIndex` 的接口是留给向量版的：换掉 `_score_metadata` 一个方法即可。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .schema import TableInfo

_ASCII = re.compile(r"[a-z0-9_]+")
_CJK = re.compile(r"[\u4e00-\u9fff]+")


def tokenize(text: str) -> list[str]:
    """中英混排的轻量分词：ASCII 取词，中文取单字 + 相邻二字。"""
    lowered = text.lower()
    tokens = _ASCII.findall(lowered)
    for chunk in _CJK.findall(lowered):
        tokens.extend(chunk)
        tokens.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
    return tokens


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._n = len(docs)
        self._avgdl = sum(len(d) for d in docs) / self._n if self._n else 0.0
        self._tf = [Counter(d) for d in docs]
        self._dl = [len(d) for d in docs]

        df: Counter = Counter()
        for doc in docs:
            df.update(set(doc))
        self._idf = {
            term: math.log(1 + (self._n - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def scores(self, query: list[str]) -> list[float]:
        out = []
        for tf, dl in zip(self._tf, self._dl, strict=True):
            total = 0.0
            for term in query:
                freq = tf.get(term)
                if not freq:
                    continue
                norm = freq * (self._k1 + 1) / (
                    freq + self._k1 * (1 - self._b + self._b * dl / (self._avgdl or 1))
                )
                total += self._idf.get(term, 0.0) * norm
            out.append(total)
        return out


def _ranks(scores: list[float]) -> list[int | None]:
    """返回每个下标的名次（0 起）；**零分的视为没命中，返回 None**。

    这一点很关键：如果把零分文档也排进名次，它们会按原顺序拿到
    1/(60+0)、1/(60+1)… 这样的分数，等于给字母序靠前的表一个固定优势
    —— 实测会让 as_complaint、as_return_apply 这类无关表挤掉 refunds。
    """
    hit = [i for i, score in enumerate(scores) if score > 0]
    order = sorted(hit, key=lambda i: (-scores[i], i))
    ranks: list[int | None] = [None] * len(scores)
    for rank, idx in enumerate(order):
        ranks[idx] = rank
    return ranks


def rrf(rankings: list[list[int | None]], k: int = 60) -> list[float]:
    fused = [0.0] * (len(rankings[0]) if rankings else 0)
    for ranks in rankings:
        for idx, rank in enumerate(ranks):
            if rank is not None:
                fused[idx] += 1.0 / (k + rank + 1)
    return fused


@dataclass(slots=True)
class TableDoc:
    table: TableInfo
    metadata_tokens: list[str]
    value_tokens: list[str]


class TableIndex:
    def __init__(self, tables: list[TableInfo]) -> None:
        self.docs = [
            TableDoc(
                table=t,
                metadata_tokens=tokenize(self._metadata_text(t)),
                value_tokens=tokenize(self._value_text(t)),
            )
            for t in tables
        ]
        self._meta = BM25([d.metadata_tokens for d in self.docs])
        self._value = BM25([d.value_tokens for d in self.docs])

    @staticmethod
    def _metadata_text(table: TableInfo) -> str:
        parts = [table.name, table.comment]
        for col in table.columns:
            parts.extend([col.name, col.note])
        return " ".join(part for part in parts if part)

    @staticmethod
    def _value_text(table: TableInfo) -> str:
        return " ".join(value for values in table.enums.values() for value in values)

    def rank(self, question: str) -> list[tuple[str, float]]:
        """返回按相关度降序的 (表名, RRF 分数)。"""
        query = tokenize(question)
        meta_scores = self._meta.scores(query)
        value_scores = self._value.scores(query)
        fused = rrf([_ranks(meta_scores), _ranks(value_scores)])
        order = sorted(range(len(fused)), key=lambda i: (-fused[i], i))
        return [(self.docs[i].table.name, fused[i]) for i in order]

    def select(self, tables: list[TableInfo], question: str, top_k: int) -> list[TableInfo]:
        """挑出 top_k 张表，按原 schema 顺序返回（保证 prompt 稳定）。"""
        ranked = self.rank(question)[:top_k]
        keep = {name for name, _ in ranked}
        return [t for t in tables if t.name in keep]
