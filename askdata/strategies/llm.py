"""P1/P3 的 LLM 策略。

P1：全量 schema 注入 + 零样本，一次生成，错了就错了。
P3 的自我纠正在 `selfcorrect.py` 里，复用这里的 prompt 与客户端。
P8：动态 few-shot —— 从题库里检索最相似的问题连同标准 SQL 一起注入。
"""

from __future__ import annotations

import re
import time
from typing import Callable, Sequence

from ..llm import DeepSeekClient, LLMResponse
from ..models import Question
from ..retrieval import BM25, tokenize
from ..schema import BUSINESS_RULES

SchemaProvider = Callable[[Question], str]

SYSTEM_PROMPT = "你是一名资深数据分析师，只输出 SQL，不输出任何解释。"

USER_TEMPLATE = """\
你是电商公司的数据分析师。请把用户的问题翻译成一条可以在 DuckDB 上执行的 SQL。

## 数据库结构
{schema}

## 业务口径（必须遵守）
{rules}
{examples}
## 输出要求
1. 只输出一条 SELECT 语句，放在 ```sql 代码块中，不要任何解释。
2. 使用 DuckDB 方言（日期用 TIMESTAMP '2025-01-01' 这种写法，按月用 strftime(ts, '%Y-%m')）。
3. 结果列的顺序与问题中提到的顺序一致。
4. 表名、列名必须真实存在，不要编造。

用户问题：{question}"""

EXAMPLES_HEADER = """
## 参考示例（同类问题 → 标准 SQL，仅供模仿写法）
{items}
"""


class ExampleBank:
    """题库检索器：给一个问题找出最相似的历史问答。

    真实系统里这批示例来自分析师的查询日志；这里直接用评测集的 50 道题，
    评测时**必须 leave-one-out**（把当前题从示例池里剔除），否则就是把答案
    直接喂给模型——这一点在报告里也有说明。
    """

    def __init__(self, questions: Sequence[Question], k1: float = 1.5, b: float = 0.75) -> None:
        self.questions = list(questions)
        self.docs = [tokenize(q.question) for q in self.questions]
        self.bm25 = BM25(self.docs, k1=k1, b=b)

    def retrieve(self, question: Question, k: int) -> list[Question]:
        if k <= 0 or not self.questions:
            return []
        scores = self.bm25.scores(tokenize(question.question))
        ranked = sorted(range(len(self.questions)), key=lambda i: scores[i], reverse=True)
        picked: list[Question] = []
        for i in ranked:
            candidate = self.questions[i]
            if candidate.id == question.id:  # leave-one-out
                continue
            if scores[i] <= 0:
                break
            picked.append(candidate)
            if len(picked) >= k:
                break
        return picked


_FENCE = re.compile(r"```(?:sql)?\s*(.+?)```", re.S | re.I)
_KEYWORD = re.compile(r"\b(select|with)\b", re.I)


def extract_sql(text: str) -> str | None:
    """从模型回复里抠出第一条 SQL。

    模型很爱在代码块外面写"好的，这是查询："，也很爱一次给多条语句。
    沙箱只接受单条 SELECT，所以这里取第一段并以第一个分号截断。
    """
    blocks = [m.group(1) for m in _FENCE.finditer(text)] or [text]
    for block in blocks:
        match = _KEYWORD.search(block)
        if not match:
            continue
        sql = block[match.start():].strip()
        sql = sql.split(";")[0].strip()
        if sql:
            return sql
    return None


class LLMStrategy:
    name = "llm"

    def __init__(
        self,
        schema_provider: SchemaProvider,
        model: str | None = None,
        use_cache: bool = True,
        example_bank: ExampleBank | None = None,
        fewshot: int = 0,
    ) -> None:
        self._schema_provider = schema_provider
        self._client = DeepSeekClient(model=model, use_cache=use_cache)
        self.model = self._client.model
        self._bank = example_bank
        self._fewshot = fewshot
        self.last_examples: list[str] = []
        self.usage = {
            "model": self.model,
            "calls": 0,
            "cache_hits": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "parse_failures": 0,
            "api_errors": 0,
            "latency_ms": 0,
        }

    def _examples_block(self, question: Question) -> str:
        if not self._bank or self._fewshot <= 0:
            self.last_examples = []
            return ""
        examples = self._bank.retrieve(question, self._fewshot)
        self.last_examples = [e.id for e in examples]
        if not examples:
            return ""
        items = "\n\n".join(
            f"### {e.question}\n```sql\n{e.gold_sql.strip()}\n```" for e in examples
        )
        return EXAMPLES_HEADER.format(items=items)

    def build_prompt(self, question: Question) -> str:
        return USER_TEMPLATE.format(
            schema=self._schema_provider(question),
            rules=BUSINESS_RULES,
            examples=self._examples_block(question),
            question=question.question,
        )

    def complete(self, user_prompt: str) -> LLMResponse | None:
        """发一次请求并累计用量；失败返回 None（计入 api_errors）。"""
        started = time.perf_counter()
        try:
            response = self._client.chat(SYSTEM_PROMPT, user_prompt, temperature=0.0, max_tokens=1024)
        except RuntimeError as exc:
            self.usage["api_errors"] += 1
            print(f"  [warn] 调用失败：{exc}")
            return None

        if response.cached:
            self.usage["cache_hits"] += 1
        else:
            self.usage["calls"] += 1
            self.usage["prompt_tokens"] += response.prompt_tokens
            self.usage["completion_tokens"] += response.completion_tokens
            self.usage["latency_ms"] += int((time.perf_counter() - started) * 1000)
        return response

    def generate(self, question: Question) -> str | None:
        response = self.complete(self.build_prompt(question))
        if response is None:
            return None
        sql = extract_sql(response.content)
        if sql is None:
            self.usage["parse_failures"] += 1
        return sql
