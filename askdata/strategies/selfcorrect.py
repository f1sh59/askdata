"""P3：执行反馈自纠。

思路很朴素：SQL 写错了就该看到错误信息再改，人就是这么干的。

循环是：生成 → 在只读沙箱里执行 → 如果报错（或结果为空）→ 把
「上一版 SQL + 数据库原文报错」追加到 prompt 里要求修正 → 再执行。

两个触发条件要分开看：

* **执行报错**：这是硬信号，重试几乎总是对的，P1 里 m10/h08 就是这类。
* **结果为空**：软信号。评测集的 gold 全部非空，所以"空结果"在这里必然意味着错了；
  但在真实产品里，用户问的可能是"上周有没有异常订单"，答案本来就该是空。
  因此是否启用由 `retry_on_empty` 控制，报告里也会分开统计。

并且**必须记录第一次尝试的分数**：只看最终分数会掩盖"改坏了"的情况。
"""

from __future__ import annotations

from ..db import QueryResult, Sandbox
from ..models import Question
from .llm import LLMStrategy, extract_sql

CORRECTION_TEMPLATE = """

## 上一次尝试
```sql
{previous_sql}
```

{feedback}

请修正后重新输出一条完整的 SELECT 语句（仍然只输出 ```sql 代码块，不要解释）。
不要重复上一版的错误。"""


def build_feedback(result: QueryResult) -> str:
    if result.error:
        return f"这条 SQL 执行失败了，数据库返回的报错原文：\n{result.error}"
    return "这条 SQL 执行成功了，但返回 0 行。结果很可能是过滤条件（状态值、时间范围）或关联条件写错了。"


class SelfCorrectingStrategy:
    name = "llm-selfcorrect"

    def __init__(
        self,
        base: LLMStrategy,
        sandbox: Sandbox,
        max_attempts: int = 3,
        retry_on_empty: bool = True,
    ) -> None:
        self._base = base
        self._sandbox = sandbox
        self._max_attempts = max_attempts
        self._retry_on_empty = retry_on_empty
        self.attempts: dict[str, list[str]] = {}
        self.rounds: dict[str, int] = {}
        self.model = base.model

    @property
    def usage(self) -> dict[str, object]:
        return self._base.usage

    def generate(self, question: Question) -> str | None:
        prompt = self._base.build_prompt(question)
        history: list[str] = []

        for _ in range(self._max_attempts):
            response = self._base.complete(prompt)
            if response is None:
                break

            sql = extract_sql(response.content)
            if sql is None:
                self._base.usage["parse_failures"] += 1
                break
            history.append(sql)

            result = self._sandbox.run(sql)
            retry_reason = self._should_retry(result)
            if retry_reason is None:
                break

            prompt = self._base.build_prompt(question) + CORRECTION_TEMPLATE.format(
                previous_sql=sql, feedback=retry_reason
            )

        self.attempts[question.id] = history
        self.rounds[question.id] = len(history)
        return history[-1] if history else None

    def _should_retry(self, result: QueryResult) -> str | None:
        if result.error:
            return f"这条 SQL 执行失败了，数据库返回的报错原文：\n{result.error}"
        if not result.rows and self._retry_on_empty:
            return build_feedback(result)
        return None
