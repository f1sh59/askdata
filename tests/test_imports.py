from __future__ import annotations

import json
import unittest

from askdata import imports

CSV = """城市,年份,销售额,登记日期,是否退货
"杭州",2025,"1,234.50",2025/01/05,是
上海,2025,￥2000,2025-01-06,否
 杭州 ,2025,"1,234.50",2025/01/05,是
,,,,
北京,2024,3000.756,2024年3月1日,是
"""


class ParseTest(unittest.TestCase):
    def test_csv_with_quoted_commas(self) -> None:
        headers, rows = imports.parse_payload(CSV)
        self.assertEqual(headers[0], "城市")
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0][2], "1,234.50")

    def test_tsv_is_detected(self) -> None:
        headers, rows = imports.parse_payload("a\tb\tc\n1\t2\t3\n4\t5\t6")
        self.assertEqual(headers, ["a", "b", "c"])
        self.assertEqual(rows[1], ["4", "5", "6"])

    def test_json_array(self) -> None:
        payload = json.dumps([{"城市": "杭州", "销售额": 1234.5}, {"城市": "上海", "销售额": 2000}])
        headers, rows = imports.parse_payload(payload)
        self.assertEqual(headers, ["城市", "销售额"])
        self.assertEqual(rows[0][1], 1234.5)

    def test_empty_input_raises(self) -> None:
        with self.assertRaises(ValueError):
            imports.parse_payload("   ")


class CleanTest(unittest.TestCase):
    def test_header_normalization(self) -> None:
        self.assertEqual(imports.normalize_header(" 销售额 (元) ", 0), "销售额_元")
        self.assertEqual(imports.normalize_header("", 3), "col_4")

    def test_number_cleaning(self) -> None:
        self.assertEqual(imports.clean_number("￥1,234.50"), "1234.50")
        self.assertEqual(imports.clean_number(" 2 000 元 "), "2000")

    def test_date_formats(self) -> None:
        self.assertIsNotNone(imports.parse_date("2025-01-05"))
        self.assertIsNotNone(imports.parse_date("2025/01/05"))
        self.assertIsNotNone(imports.parse_date("2025年1月5日"))
        self.assertIsNone(imports.parse_date("不是日期"))


class InferTest(unittest.TestCase):
    def test_types(self) -> None:
        opts = dict(imports.DEFAULT_OPTIONS)
        self.assertEqual(imports.infer_type(["1", "2", "3"], opts), "BIGINT")
        self.assertEqual(imports.infer_type(["1.5", "2", "￥3,000"], opts), "DOUBLE")
        self.assertEqual(imports.infer_type(["2025-01-01", "2025/02/03"], opts), "DATE")
        self.assertEqual(imports.infer_type(["是", "否", "是"], opts), "BOOLEAN")
        self.assertEqual(imports.infer_type(["杭州", "上海"], opts), "VARCHAR")

    def test_negative_and_float(self) -> None:
        opts = dict(imports.DEFAULT_OPTIONS)
        self.assertEqual(imports.infer_type(["-1", "0"], opts), "BIGINT")
        self.assertEqual(imports.infer_type(["-1.5", "0"], opts), "DOUBLE")


class PreviewTest(unittest.TestCase):
    def test_dedupe_and_empty_rows(self) -> None:
        result = imports.preview(CSV)
        self.assertTrue(result["ok"])
        self.assertEqual(result["stats"]["raw_rows"], 5)
        self.assertEqual(result["stats"]["duplicates_removed"], 1)
        self.assertEqual(result["stats"]["rows"], 3)

    def test_column_types(self) -> None:
        result = imports.preview(CSV)
        types = {c["name"]: c["type"] for c in result["columns"]}
        self.assertEqual(types["年份"], "BIGINT")
        self.assertEqual(types["销售额"], "DOUBLE")
        self.assertEqual(types["登记日期"], "DATE")
        self.assertEqual(types["是否退货"], "BOOLEAN")

    def test_options_can_disable_cleaning(self) -> None:
        result = imports.preview(CSV, {"dedupe": False, "drop_empty_rows": False})
        self.assertEqual(result["stats"]["rows"], 5)


class TableNameTest(unittest.TestCase):
    def test_prefix_and_sanitize(self) -> None:
        self.assertEqual(imports.sanitize_table_name("城市销售"), "imp_城市销售")
        self.assertTrue(imports.sanitize_table_name("2025 sales").startswith("imp_"))
        self.assertNotIn(" ", imports.sanitize_table_name("a b c"))


class BuildTest(unittest.TestCase):
    def test_ddl_and_rows(self) -> None:
        specs = [
            {"name": "城市", "type": "VARCHAR", "include": True},
            {"name": "销售额", "type": "DOUBLE", "include": True},
            {"name": "年份", "type": "BIGINT", "include": False},
        ]
        ddl = imports.build_ddl("imp_t", specs)
        self.assertIn('"城市" VARCHAR', ddl)
        self.assertNotIn("年份", ddl)

        names, rows = imports.build_rows(CSV, {}, specs)
        self.assertEqual(names, ["城市", "销售额"])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0][1], 1234.5)

    def test_all_columns_excluded_raises(self) -> None:
        with self.assertRaises(ValueError):
            imports.build_ddl("imp_t", [{"name": "a", "type": "VARCHAR", "include": False}])


class DuplicateHeaderTest(unittest.TestCase):
    """验收时发现的 bug：重名列会让建表直接报数据库错。"""

    def test_duplicate_headers_are_renamed(self) -> None:
        self.assertEqual(imports.dedupe_headers(["a", "a", "b"]), ["a", "a_1", "b"])

    def test_collision_after_normalization(self) -> None:
        result = imports.preview("a b,a_b\n1,2")
        self.assertEqual([c["name"] for c in result["columns"]], ["a_b", "a_b_1"])

    def test_duplicate_headers_can_build_ddl(self) -> None:
        specs = [
            {"name": "a", "type": "BIGINT", "include": True},
            {"name": "a_1", "type": "BIGINT", "include": True},
        ]
        self.assertIn('"a_1"', imports.build_ddl("imp_t", specs))


class EmptyDataTest(unittest.TestCase):
    """验收时发现的 bug：只有表头时，直到写库才报底层异常。"""

    def test_headers_only_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            imports.preview("x,y")

    def test_all_rows_filtered_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            imports.preview("x,y\n,,\n,,")

    def test_single_row_still_works(self) -> None:
        result = imports.preview("x,y\n1,2")
        self.assertEqual(result["stats"]["rows"], 1)

if __name__ == "__main__":
    unittest.main()
