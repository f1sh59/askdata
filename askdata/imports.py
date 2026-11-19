"""数据导入与整理：把用户上传的 CSV / TSV / JSON 变成能直接提问的表。

企业真实数据的麻烦不在"存进去"，在"存之前"：分隔符不对、数字带千分位、
日期有五种写法、空值是空串、表头带空格和括号、整行重复。所以这里做两件事：

1. **解析**：自动识别分隔符（CSV/TSV/分号）与 JSON 数组；
2. **整理**：表头规范化、去空白、空串转 NULL、数字清洗、日期识别、去重、删空行，
   最后按列推断类型（整数/小数/日期/布尔/文本），也可以手动改。
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime

# ---------------------------------------------------------------- 选项


DEFAULT_OPTIONS: dict[str, bool] = {
    "trim": True,            # 去掉首尾空白
    "empty_as_null": True,   # 空串 / "-" / "N/A" 视为 NULL
    "clean_numbers": True,   # 去掉千分位与货币符号
    "parse_dates": True,     # 识别多种日期写法
    "dedupe": True,          # 整行去重
    "drop_empty_rows": True, # 删除全空行
    "normalize_headers": True,
}

NULL_TOKENS = {"", "-", "--", "null", "NULL", "None", "N/A", "n/a", "NA", "nan", "NaN", "无"}

CURRENCY = "￥$€£¥"
DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d",
                "%Y年%m月%d日", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S")

TYPE_LABELS = {
    "BIGINT": "整数",
    "DOUBLE": "小数",
    "DATE": "日期",
    "BOOLEAN": "布尔",
    "VARCHAR": "文本",
}


@dataclass(slots=True)
class ColumnSpec:
    name: str
    type: str
    include: bool = True
    nulls: int = 0
    samples: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 解析


def detect_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:20])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        counts = {d: sample.count(d) for d in [",", "\t", ";", "|"]}
        best = max(counts, key=lambda d: counts[d])
        return best if counts[best] else ","


def parse_payload(text: str) -> tuple[list[str], list[list[object]]]:
    """返回 (表头, 数据行)。支持 CSV/TSV/分号分隔，以及 JSON 数组。"""
    raw = text.lstrip("\ufeff").strip()
    if not raw:
        raise ValueError("内容是空的")

    if raw[0] in "[{":
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list) or not data:
            raise ValueError("JSON 需要是非空数组")
        if not all(isinstance(item, dict) for item in data):
            raise ValueError("JSON 数组里每个元素都必须是对象")
        headers: list[str] = []
        for item in data:
            for key in item:
                if key not in headers:
                    headers.append(key)
        rows = [[item.get(h) for h in headers] for item in data]
        return headers, rows

    delimiter = detect_delimiter(raw)
    reader = csv.reader(io.StringIO(raw), delimiter=delimiter)
    rows = [row for row in reader]
    if not rows:
        raise ValueError("没有解析到任何行")
    headers = rows[0]
    return headers, rows[1:]


# ---------------------------------------------------------------- 清洗


def normalize_header(name: str, index: int) -> str:
    cleaned = re.sub(r"[\s\(\)（）\[\]【】/\\]+", "_", str(name).strip())
    cleaned = re.sub(r"[^\w\u4e00-\u9fa5]", "_", cleaned).strip("_")
    cleaned = re.sub(r"_{2,}", "_", cleaned)
    return cleaned or f"col_{index + 1}"


def clean_number(value: str) -> str | None:
    """去掉千分位、货币符号和全角字符，返回可解析的数字字符串。"""
    text = value.strip().replace(",", "").replace("，", "").replace(" ", "")
    text = text.replace("％", "%")
    for symbol in CURRENCY:
        text = text.replace(symbol, "")
    text = text.replace("元", "")
    return text or None


def parse_date(value: str) -> date | None:
    text = value.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def clean_cell(value: object, options: dict[str, bool]) -> object:
    if value is None:
        return None
    if isinstance(value, (int, float, bool)):
        return value
    text = str(value)
    if options.get("trim"):
        text = text.strip()
    if options.get("empty_as_null") and text in NULL_TOKENS:
        return None
    return text


# ---------------------------------------------------------------- 类型推断


def infer_type(values: list[object], options: dict[str, bool]) -> str:
    present = [v for v in values if v is not None and str(v).strip() != ""]
    if not present:
        return "VARCHAR"

    def all_match(fn) -> bool:
        return all(fn(str(v).strip()) for v in present)

    if all_match(lambda s: re.fullmatch(r"[+-]?\d+", s)):
        return "BIGINT"

    if options.get("clean_numbers"):
        cleaned = []
        for v in present:
            c = clean_number(str(v).strip())
            if c is None:
                return "VARCHAR"
            cleaned.append(c)
        if all(re.fullmatch(r"[+-]?\d+", c) for c in cleaned):
            return "BIGINT"
        if all(re.fullmatch(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?", c) for c in cleaned):
            return "DOUBLE"

    if options.get("parse_dates") and all(parse_date(str(v)) is not None for v in present):
        return "DATE"

    boolean_like = {"true", "false", "是", "否", "y", "n", "0", "1"}
    lowered = {str(v).strip().lower() for v in present}
    if lowered and lowered <= boolean_like and len(lowered) <= 2:
        return "BOOLEAN"

    return "VARCHAR"


def convert_value(value: object, sql_type: str, options: dict[str, bool]) -> object:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float, bool)) and not isinstance(value, bool):
        return value
    text = str(value).strip()
    if text in NULL_TOKENS:
        return None
    if sql_type == "BIGINT":
        cleaned = clean_number(text) if options.get("clean_numbers") else text
        try:
            if cleaned is None:
                raise TypeError("空值")
            return int(float(cleaned))
        except (TypeError, ValueError):
            return None
    if sql_type == "DOUBLE":
        cleaned = clean_number(text) if options.get("clean_numbers") else text
        try:
            if cleaned is None:
                raise TypeError("空值")
            return float(cleaned)
        except (TypeError, ValueError):
            return None
    if sql_type == "DATE":
        parsed = parse_date(text)
        return parsed.isoformat() if parsed else None
    if sql_type == "BOOLEAN":
        return text.lower() in {"true", "是", "y", "1"}
    return text


# ---------------------------------------------------------------- 预览 / 导入


def dedupe_headers(headers: list[str]) -> list[str]:
    """列名去重：`a, a, a` → `a, a_2, a_3`。

    真实 CSV 里重名列很常见（导出工具重复带主键、合并表头残留），
    而 DuckDB 建表时重名会直接报 CatalogException。
    在预览阶段就修掉，比让用户看到一条数据库报错好。
    """
    seen: dict[str, int] = {}
    out: list[str] = []
    for name in headers:
        base = name
        if base in seen:
            seen[base] += 1
            candidate = f"{base}_{seen[base]}"
            while candidate in seen:
                seen[base] += 1
                candidate = f"{base}_{seen[base]}"
            seen.setdefault(candidate, 0)
            out.append(candidate)
        else:
            seen[base] = 0
            out.append(base)
    return out


def prepare(text: str, options: dict[str, bool]) -> tuple[list[str], list[list[object]], list[ColumnSpec], dict]:
    headers, raw_rows = parse_payload(text)
    headers = [normalize_header(h, i) if options.get("normalize_headers") else str(h).strip()
               for i, h in enumerate(headers)]
    headers = dedupe_headers(headers)

    rows: list[list[object]] = []
    for raw in raw_rows:
        cells = [clean_cell(v, options) for v in raw]
        # 补齐/截断到表头长度
        if len(cells) < len(headers):
            cells += [None] * (len(headers) - len(cells))
        cells = cells[: len(headers)]
        if options.get("drop_empty_rows") and all(c is None for c in cells):
            continue
        rows.append(cells)

    duplicates = 0
    if options.get("dedupe"):
        seen = set()
        unique = []
        for row in rows:
            key = tuple(row)
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            unique.append(row)
        rows = unique

    if not rows:
        raise ValueError("没有可导入的数据行（可能只有表头，或者整理选项把所有行都过滤掉了）")

    columns: list[ColumnSpec] = []
    for idx, name in enumerate(headers):
        values = [row[idx] for row in rows]
        sql_type = infer_type(values, options)
        nulls = sum(1 for v in values if v is None)
        samples = [str(v) for v in values if v is not None][:3]
        columns.append(ColumnSpec(name=name, type=sql_type, nulls=nulls, samples=samples))

    stats = {
        "raw_rows": len(raw_rows),
        "rows": len(rows),
        "duplicates_removed": duplicates,
        "empty_rows_removed": len(raw_rows) - len(rows) - duplicates if len(raw_rows) >= len(rows) else 0,
        "columns": len(headers),
    }
    return headers, rows, columns, stats


def preview(text: str, options: dict[str, bool] | None = None) -> dict:
    opts = {**DEFAULT_OPTIONS, **(options or {})}
    headers, rows, columns, stats = prepare(text, opts)
    sample = [[("" if v is None else str(v)) for v in row] for row in rows[:8]]
    return {
        "ok": True,
        "headers": headers,
        "columns": [
            {
                "name": c.name,
                "type": c.type,
                "type_label": TYPE_LABELS.get(c.type, c.type),
                "nulls": c.nulls,
                "samples": c.samples,
            }
            for c in columns
        ],
        "sample": sample,
        "stats": stats,
        "preview_rows": len(sample),
    }


def sanitize_table_name(name: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fa5]", "_", (name or "").strip())
    cleaned = re.sub(r"_{2,}", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = "dataset"
    if not re.match(r"^[A-Za-z_\u4e00-\u9fa5]", cleaned):
        cleaned = f"t_{cleaned}"
    return f"imp_{cleaned}"[:60]


def build_ddl(table: str, columns: list[dict]) -> str:
    parts = [f'"{c["name"]}" {c["type"]}' for c in columns if c.get("include", True)]
    if not parts:
        raise ValueError("至少要保留一列")
    return f'CREATE OR REPLACE TABLE "{table}" ({", ".join(parts)})'


def build_rows(
    text: str,
    options: dict[str, bool],
    column_specs: list[dict],
) -> tuple[list[str], list[list[object]]]:
    """按用户最终确认的列顺序与类型，把整份数据转成可插入的行。"""
    opts = {**DEFAULT_OPTIONS, **(options or {})}
    headers, rows, inferred, _ = prepare(text, opts)
    by_name = {c.name: c for c in inferred}

    chosen = [c for c in column_specs if c.get("include", True)]
    if not chosen:
        raise ValueError("至少要保留一列")

    positions = []
    for spec in chosen:
        name = spec["name"]
        if name in headers:
            positions.append(headers.index(name))
        elif name in by_name:
            continue
        else:
            raise ValueError(f"找不到列：{name}")

    out = []
    for row in rows:
        out.append([convert_value(row[i], spec["type"], opts) for i, spec in zip(positions, chosen, strict=True)])
    return [c["name"] for c in chosen], out
