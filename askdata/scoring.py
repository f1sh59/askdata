"""结果集比对 —— Execution Accuracy 的实现。

两个指标（这是本项目最容易被问到的细节）：

* EX（严格）：行多重集完全一致，且列数一致。列顺序不参与比较，
  因为 SQL 的 SELECT 顺序不影响结论，但列数不同说明查询"回答了另一个问题"。
* EX-lenient：在严格基础上额外忽略行内列顺序，用来识别"值都对、只是列序不同"
  的情况。它只作为辅助指标，主指标永远是严格 EX。

数值统一四舍五入到 2 位再比较，避免浮点误差把 23668264.60 判成错误。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from .db import QueryResult

# 失败类型，报告里按这个分桶
CORRECT = "correct"
CORRECT_LENIENT = "correct_lenient"   # 严格失败、忽略列序后正确
SQL_ERROR = "sql_error"
EMPTY_RESULT = "empty_result"         # 执行成功但 0 行
COLUMN_MISMATCH = "column_mismatch"   # 列数不同
VALUE_MISMATCH = "value_mismatch"     # 形状相同、值不同
NO_SQL = "no_sql"                     # 策略没有产出 SQL

STATUS_LABELS = {
    CORRECT: "正确",
    CORRECT_LENIENT: "正确（仅列顺序不同）",
    SQL_ERROR: "SQL 执行失败",
    EMPTY_RESULT: "结果为空",
    COLUMN_MISMATCH: "列数不一致",
    VALUE_MISMATCH: "结果值不一致",
    NO_SQL: "未产出 SQL",
}


@dataclass(slots=True)
class Outcome:
    status: str
    gold_row_count: int = 0
    pred_row_count: int = 0

    @property
    def is_correct(self) -> bool:
        return self.status == CORRECT

    @property
    def is_lenient_correct(self) -> bool:
        return self.status in (CORRECT, CORRECT_LENIENT)


def normalize_value(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return round(float(value), 2)
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, int):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, str):
        return value.strip()
    return value


def _canonical_rows(rows: list[tuple], *, sort_within_row: bool = False) -> Counter:
    out: Counter = Counter()
    for row in rows:
        values = tuple(normalize_value(v) for v in row)
        if sort_within_row:
            values = tuple(sorted(values, key=repr))
        out[values] += 1
    return out


def score(gold: QueryResult, pred: QueryResult | None) -> Outcome:
    if pred is None:
        return Outcome(NO_SQL)
    if pred.error is not None:
        return Outcome(SQL_ERROR, len(gold.rows), 0)
    if not gold.ok:
        # gold SQL 自己跑不通属于评测集 bug，不能算模型的错
        raise ValueError(f"gold SQL 执行失败: {gold.error}")

    if len(gold.columns) != len(pred.columns):
        return Outcome(COLUMN_MISMATCH, len(gold.rows), len(pred.rows))

    gold_counter = _canonical_rows(gold.rows)
    pred_counter = _canonical_rows(pred.rows)

    if gold_counter == pred_counter:
        return Outcome(CORRECT, len(gold.rows), len(pred.rows))
    if pred.rows and _canonical_rows(gold.rows, sort_within_row=True) == _canonical_rows(
        pred.rows, sort_within_row=True
    ):
        return Outcome(CORRECT_LENIENT, len(gold.rows), len(pred.rows))
    if not pred.rows:
        return Outcome(EMPTY_RESULT, len(gold.rows), 0)
    return Outcome(VALUE_MISMATCH, len(gold.rows), len(pred.rows))
