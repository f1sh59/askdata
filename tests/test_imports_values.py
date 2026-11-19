"""导入解析的边界：值转换、异常 JSON、脏数字。

`convert_value` 是"脏数据能不能进库"的最后一道关：
空串、NULL 字样、带千分位的金额、乱写的日期、布尔的各种写法。
这些分支平时走不到，但真实 CSV 里到处都是。
"""

from __future__ import annotations

import unittest

from askdata.imports import convert_value, preview


class ConvertValueTest(unittest.TestCase):
    def setUp(self) -> None:
        self.options = {"clean_numbers": True}

    def test_empty_string_is_null(self) -> None:
        self.assertIsNone(convert_value("", "VARCHAR", self.options))

    def test_null_tokens_are_null(self) -> None:
        for token in ("NULL", "null", "N/A", "-", "无"):
            self.assertIsNone(convert_value(token, "VARCHAR", self.options), token)

    def test_native_numbers_pass_through(self) -> None:
        self.assertEqual(convert_value(42, "BIGINT", self.options), 42)
        self.assertAlmostEqual(convert_value(3.5, "DOUBLE", self.options), 3.5)

    def test_bigint_with_thousands_separator(self) -> None:
        self.assertEqual(convert_value("1,234", "BIGINT", self.options), 1234)

    def test_bigint_from_float_string(self) -> None:
        self.assertEqual(convert_value("1234.9", "BIGINT", self.options), 1234)

    def test_bad_number_returns_none(self) -> None:
        """解析不了就置空，而不是把 "abc" 塞进 BIGINT 让整批导入失败。"""
        self.assertIsNone(convert_value("abc", "BIGINT", self.options))

    def test_double_with_currency_symbol(self) -> None:
        self.assertAlmostEqual(convert_value("¥1,234.50", "DOUBLE", self.options), 1234.5)

    def test_bad_double_returns_none(self) -> None:
        self.assertIsNone(convert_value("n/a", "DOUBLE", self.options))

    def test_date_parsing(self) -> None:
        self.assertEqual(convert_value("2025-01-01", "DATE", self.options), "2025-01-01")

    def test_date_with_slash(self) -> None:
        self.assertEqual(convert_value("2025/01/01", "DATE", self.options), "2025-01-01")

    def test_unparseable_date_returns_none(self) -> None:
        self.assertIsNone(convert_value("不是日期", "DATE", self.options))

    def test_boolean_variants(self) -> None:
        for text in ("true", "是", "Y", "1"):
            self.assertTrue(convert_value(text, "BOOLEAN", self.options), text)
        for text in ("false", "否", "n", "0"):
            self.assertFalse(convert_value(text, "BOOLEAN", self.options), text)

    def test_without_cleaning_numbers(self) -> None:
        """关掉清洗时，"1,234" 不是合法整数 → 置空而不是瞎猜。"""
        self.assertIsNone(convert_value("1,234", "BIGINT", {"clean_numbers": False}))


class JsonPreviewTest(unittest.TestCase):
    def test_object_is_wrapped_into_list(self) -> None:
        result = preview('{"a": 1, "b": 2}')
        self.assertGreaterEqual(len(result["columns"]), 2)

    def test_array_of_objects(self) -> None:
        result = preview('[{"a": 1}, {"a": 2}]')
        self.assertIn("a", [c["name"] for c in result["columns"]])

    def test_empty_array_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            preview("[]")

    def test_non_object_elements_rejected(self) -> None:
        with self.assertRaises(ValueError):
            preview("[1, 2, 3]")

    def test_csv_with_header(self) -> None:
        result = preview("a,b\n1,2\n3,4")
        self.assertEqual([c["name"] for c in result["columns"]], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
