from __future__ import annotations

import unittest
from decimal import Decimal

from askdata.db import QueryResult
from askdata.models import load_dataset
from askdata.scoring import (
    COLUMN_MISMATCH,
    CORRECT,
    CORRECT_LENIENT,
    EMPTY_RESULT,
    NO_SQL,
    SQL_ERROR,
    VALUE_MISMATCH,
    normalize_value,
    score,
)
from askdata.config import DEFAULT_QUESTIONS


def make(rows: list[tuple], columns: list[str] | None = None, error: str | None = None) -> QueryResult:
    width = len(rows[0]) if rows else 1
    return QueryResult(columns or [f"c{i}" for i in range(width)], rows, error)


class NormalizeValueTest(unittest.TestCase):
    def test_decimal_and_float_round_to_two_places(self) -> None:
        self.assertEqual(normalize_value(Decimal("123.456")), 123.46)
        self.assertEqual(normalize_value(123.4551), 123.46)

    def test_string_is_stripped(self) -> None:
        self.assertEqual(normalize_value(" 杭州 "), "杭州")


class ScoreTest(unittest.TestCase):
    def test_row_order_does_not_matter(self) -> None:
        gold = make([(1, "a"), (2, "b")])
        pred = make([(2, "b"), (1, "a")])
        self.assertEqual(score(gold, pred).status, CORRECT)

    def test_duplicate_rows_are_a_multiset(self) -> None:
        gold = make([(1, "a"), (1, "a")])
        pred = make([(1, "a")])
        self.assertEqual(score(gold, pred).status, VALUE_MISMATCH)

    def test_column_count_mismatch(self) -> None:
        gold = make([(1, "a")])
        pred = make([(1,)])
        self.assertEqual(score(gold, pred).status, COLUMN_MISMATCH)

    def test_value_mismatch(self) -> None:
        self.assertEqual(score(make([(1,)]), make([(2,)])).status, VALUE_MISMATCH)

    def test_empty_prediction(self) -> None:
        self.assertEqual(score(make([(1,)]), make([])).status, EMPTY_RESULT)

    def test_sql_error(self) -> None:
        self.assertEqual(score(make([(1,)]), make([], error="ParserError: x")).status, SQL_ERROR)

    def test_no_sql(self) -> None:
        self.assertEqual(score(make([(1,)]), None).status, NO_SQL)

    def test_decimal_vs_float_is_equal(self) -> None:
        self.assertEqual(score(make([(Decimal("23668264.60"),)]), make([(23668264.599999,)])).status, CORRECT)

    def test_column_order_only_failure_is_reported_leniently(self) -> None:
        gold = make([("天猫", 100, 2.5)])
        pred = make([(100, "天猫", 2.5)])
        outcome = score(gold, pred)
        self.assertEqual(outcome.status, CORRECT_LENIENT)
        self.assertTrue(outcome.is_lenient_correct)
        self.assertFalse(outcome.is_correct)

    def test_null_equals_null(self) -> None:
        self.assertEqual(score(make([(None,)]), make([(None,)])).status, CORRECT)


class DatasetTest(unittest.TestCase):
    def test_dataset_shape(self) -> None:
        dataset = load_dataset(DEFAULT_QUESTIONS)
        self.assertEqual(len(dataset.questions), 50)
        by_diff = dataset.by_difficulty()
        self.assertEqual(len(by_diff["easy"]), 10)
        self.assertEqual(len(by_diff["medium"]), 20)
        self.assertEqual(len(by_diff["hard"]), 20)

    def test_cohorts_partition_the_dataset(self) -> None:
        dataset = load_dataset(DEFAULT_QUESTIONS)
        cohorts = {q.cohort for q in dataset.questions}
        self.assertEqual(cohorts, {"core30", "ext20"})
        self.assertEqual(sum(1 for q in dataset.questions if q.cohort == "core30"), 30)
        self.assertEqual(sum(1 for q in dataset.questions if q.cohort == "ext20"), 20)

    def test_gold_sql_mentions_business_rules(self) -> None:
        """带 business_rule 标签的题，gold SQL 里必须真的出现状态过滤。"""
        dataset = load_dataset(DEFAULT_QUESTIONS)
        for q in dataset.questions:
            if "business_rule" in q.tags and "退款" not in q.question and "支付" not in q.question:
                self.assertIn("已支付", q.gold_sql, q.id)


if __name__ == "__main__":
    unittest.main()
