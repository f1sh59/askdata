"""结果校验 Agent：在把数字交给人之前，先自己看一眼合不合理。

为什么要这个：
    Text2SQL 最危险的错误不是"SQL 报错"，是**跑成功了但数字是错的**。
    典型来源是对多明细订单直接 JOIN 后 SUM(pay_amount)，金额被按明细行数放大——
    这类错误不会抛异常，界面上就是一张看起来很正常的表。

    所以这里做两层：
    1. **确定性检查**（零成本、每次都跑）：空结果、负数、比率越界、数值超出全库口径；
    2. **可选的大模型复核**（花钱、按需开）：把问题和结果摘要交给模型，问它"这看起来对吗"。

    确定性检查负责兜住已知的坑（尤其是 join 扇出），LLM 复核负责发现没预料到的怪。
"""

from __future__ import annotations

from dataclasses import dataclass

# 这些别名暗示"金额 / 数量 / 比率"，用来判断某个数值列该怎么校验
AMOUNT_HINTS = ("金额", "销售额", "gmv", "amt", "amount", "revenue", "price", "额")
COUNT_HINTS = ("数", "cnt", "count", "件数", "笔数", "人数")
RATE_HINTS = ("率", "占比", "ratio", "rate", "percent", "pct")


@dataclass(slots=True)
class Issue:
    level: str          # "warn" | "info"
    code: str
    message: str

    def as_dict(self) -> dict:
        return {"level": self.level, "code": self.code, "message": self.message}


def _numeric_columns(columns: list[str], rows: list[list]) -> dict[int, list[float]]:
    """找出所有可以当数字比较的列。"""
    out: dict[int, list[float]] = {}
    for idx in range(len(columns)):
        values = []
        for row in rows:
            value = row[idx] if idx < len(row) else None
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, (int, float)):
                values.append(float(value))
        if values:
            out[idx] = values
    return out


def _matches(name: str, hints: tuple[str, ...]) -> bool:
    lowered = name.lower()
    return any(h in lowered for h in hints)


def _asks_for_number(question: str) -> bool:
    keys = ("多少", "几", "总", "合计", "平均", "排名", "top", "最高", "最低", "率", "占比")
    return any(k in question.lower() for k in keys)


def check_result(
    question: str,
    columns: list[str],
    rows: list[list],
    *,
    total_revenue: float | None = None,
    expected_rows: int | None = None,
) -> list[Issue]:
    """对一次查询结果做确定性体检。返回空列表表示没发现问题。"""
    issues: list[Issue] = []

    # ① 空结果：任何查询返回 0 行都值得看一眼（聚合题尤其可疑）
    if not rows:
        level = "warn" if _asks_for_number(question) else "info"
        issues.append(
            Issue(
                level,
                "empty_result",
                "查询成功但结果为空——可能是时间范围或过滤条件过严，也可能是数据里确实没有。",
            )
        )

    numeric = _numeric_columns(columns, rows)

    for idx, values in numeric.items():
        name = columns[idx] if idx < len(columns) else f"第{idx + 1}列"

        # ② 不该出现的负数
        if _matches(name, COUNT_HINTS + AMOUNT_HINTS) and any(v < 0 for v in values):
            issues.append(
                Issue("warn", "negative_value", f"「{name}」出现负数，计数或金额通常不该为负。")
            )

        # ③ 比率越界：比率类列要么是 0~1，要么是 0~100
        if _matches(name, RATE_HINTS):
            biggest = max(values)
            if biggest > 100.0 + 1e-6:
                issues.append(
                    Issue(
                        "warn",
                        "rate_over_100",
                        f"「{name}」最大值 {biggest:.2f}，比率超过 100% 通常说明口径写错了"
                        "（比如把占比当成了金额）。",
                    )
                )

    # ④ 量级检查：单值金额类结果超过全库总额，几乎一定是 join 扇出
    if total_revenue and len(rows) == 1:
        for idx, values in numeric.items():
            name = columns[idx] if idx < len(columns) else ""
            if not _matches(name, AMOUNT_HINTS):
                continue
            value = values[0]
            if value > total_revenue * 3:
                issues.append(
                    Issue(
                        "warn",
                        "magnitude_fanout",
                        f"「{name}」= {value:,.2f}，超过全库有效订单总额"
                        f"（{total_revenue:,.2f}）的 3 倍。"
                        "常见原因是多明细订单 JOIN 后直接 SUM，金额被按明细行数放大了。",
                    )
                )

    # ⑤ 列数与问题里点名的指标数对不上（只在问题明确列了指标时提示）
    asked = sum(1 for k in ("销售额", "订单数", "客单价", "退款率", "毛利率", "复购率") if k in question)
    data_cols = len(columns) - (1 if columns and not _matches(columns[0], COUNT_HINTS + AMOUNT_HINTS) else 0)
    if asked >= 2 and data_cols > asked:
        issues.append(
            Issue(
                "info",
                "extra_columns",
                f"问题里点名了 {asked} 个指标，结果有 {data_cols} 个数据列，确认没有多算。",
            )
        )

    return issues


VERIFY_PROMPT = """\
你是一名数据审核员。下面是一条已执行成功的查询、它的结果摘要，以及用户的原始问题。

请判断**结果是否可能算错了**。只关注数字合理性，不要重写 SQL。

## 用户问题
{question}

## 执行的 SQL
{sql}

## 结果
列：{columns}
行数：{row_count}
前几行：
{preview}

## 全库参考
所有有效订单的实付金额总和约为 {total_revenue}

## 输出要求
只输出 JSON：{{"ok": true 或 false, "reason": "一句话说明"}}
如果结果看起来合理，ok 为 true、reason 写"无明显问题"。
"""


def build_verify_prompt(
    question: str,
    sql: str,
    columns: list[str],
    rows: list[list],
    total_revenue: float | None,
) -> str:
    preview_lines = []
    for row in rows[:5]:
        preview_lines.append(" | ".join("NULL" if v is None else str(v) for v in row))
    return VERIFY_PROMPT.format(
        question=question,
        sql=sql[:1200],
        columns=", ".join(columns),
        row_count=len(rows),
        preview="\n".join(preview_lines) or "（空）",
        total_revenue=f"{total_revenue:,.2f}" if total_revenue else "未知",
    )


def parse_verify_reply(text: str) -> Issue | None:
    """把模型复核的 JSON 回复转成 Issue；说没问题就返回 None。"""
    import json
    import re

    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if payload.get("ok") is True:
        return None
    reason = str(payload.get("reason") or "模型认为结果可疑").strip()
    return Issue("warn", "llm_review", f"复核不通过：{reason}")
