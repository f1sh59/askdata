"""多轮对话：把"那 2024 年呢？"这类追问改写成可以独立理解的问题。

单轮系统每句都当成新问题，所以追问必然失效。行业通行的做法是**先改写再生成**：

    用户："2025 年各渠道的销售额"
    模型：SQL_A

    用户："那只看天猫呢？"          ← 原文无法单独执行
    ↓ 改写
    独立问题："2025 年天猫渠道的销售额是多少？"
    ↓ 走单轮管线
    模型：SQL_B

这样多轮只是"加了一层改写"，下游的召回、生成、安全闸、评测全部复用，
不需要改任何既有逻辑——这也是它能被单独评测的原因。
"""

from __future__ import annotations

import re

REWRITE_SYSTEM = "你是对话改写助手。你只输出改写后的那一句话，不输出任何解释。"

REWRITE_TEMPLATE = """\
下面是一段对话历史，用户最后一句是在追问上一个问题。
请把最后一句改写成**不依赖上下文、单独拿出来也能看懂**的完整问题。

要求：
1. 补全省略的主语、维度、时间范围；
2. 如果追问是替换条件（比如换年份、换渠道），就**替换**掉旧条件，不要叠加；
3. 保留原问题的业务口径，不要自行发挥；
4. 只输出一句**中文自然语言问题**。绝对不要输出 SQL，不要解释，不要加引号或代码块。

## 对话历史
{history}

## 用户最后一句话
{question}

改写后的问题（中文自然语言）："""

# 明显指代/省略的信号，用来决定"这句话要不要走改写"
_FOLLOWUP_HINTS = re.compile(
    r"(那|那么|呢|换成|改成|只看|再|还有|同样|一样|其他|其它|这个|那个|这些|那些|"
    r"呢？|呢\?|呢$|相比下|对比下|呢$)"
)

_SQL_START = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


def looks_like_followup(question: str) -> bool:
    """粗略判断一句是不是追问。

    宁可多改写一次（多花一次调用），也不要漏掉——漏掉必然答错，
    多改一次最多是"把完整问题原样复制一遍"。
    """
    text = (question or "").strip()
    if not text:
        return False
    if len(text) <= 8 and ("呢" in text or "那" in text):
        return True
    return bool(_FOLLOWUP_HINTS.search(text))


def render_history(turns: list[dict]) -> str:
    """只给问题，不给 SQL。

    早期版本把上一条 SQL 也塞进去，模型立刻被带进"SQL 模式"，
    把追问改写成了一条 SELECT ——历史里只留自然语言，模型才留在问答模式。
    """
    if not turns:
        return "（无）"
    return "\n".join(f"用户：{turn.get('question', '')}" for turn in turns)


def clean_rewrite(text: str) -> str | None:
    """把模型回复收成一行纯问题文本；改写成 SQL 的视为失败。"""
    if not text:
        return None
    line = text.strip()
    # 去掉常见的包裹：代码块、"改写后的问题："前缀、引号
    line = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", line).strip()
    line = re.sub(r"^(改写后的问题|独立问题|问题)[:：]\s*", "", line)
    line = line.strip("\"'“”「」")
    line = line.splitlines()[0].strip() if line else ""
    if not line or _SQL_START.match(line):
        return None
    return line
