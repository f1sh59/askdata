"""生成多轮评测集：三种机械可验证的追问类型。

为什么用"机械变换"而不是手写 20 条标答？
因为手写标答会引入人为错误，而评测集的错误会直接变成"模型背锅"。
这里每条追加题的标答都是**从基础题标答程序化变换**出来的：

* T1 换年份：把所有 2025 的时间边界换成 2024
* T2 不限时间：删掉时间过滤条件
* T3 换 TOP-N：改 LIMIT 的数字

生成后用 DuckDB 实跑一遍，要求非空且无错——机器只保证"能跑、有结果"，
语义对不对由变换规则本身保证（换年份就是换年份，不会变成别的意思）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb

DB = Path("data/askdata.duckdb")
SRC = Path("eval/questions.json")
OUT = Path("eval/multiturn_v1.json")

TIME_RANGE = re.compile(
    r"\s+AND\s+(?:\w+\.)?(order_time|register_date)\s*>=\s*(?:TIMESTAMP|DATE)\s*'[^']*'"
    r"\s+AND\s+(?:\w+\.)?(order_time|register_date)\s*<\s*(?:TIMESTAMP|DATE)\s*'[^']*'",
    re.IGNORECASE,
)


def swap_year(sql: str) -> str:
    return (
        sql.replace("TIMESTAMP '2025-01-01'", "TIMESTAMP '2024-01-01'")
        .replace("TIMESTAMP '2026-01-01'", "TIMESTAMP '2025-01-01'")
        .replace("DATE '2025-01-01'", "DATE '2024-01-01'")
        .replace("DATE '2026-01-01'", "DATE '2025-01-01'")
    )


def drop_time(sql: str) -> str | None:
    out, count = TIME_RANGE.subn("", sql)
    return out if count else None


def swap_limit(sql: str, new_n: int) -> str | None:
    if not re.search(r"\bLIMIT\s+\d+\s*$", sql.strip(), re.IGNORECASE):
        return None
    return re.sub(r"\bLIMIT\s+\d+\s*$", f"LIMIT {new_n}", sql.strip(), flags=re.IGNORECASE)


def main() -> None:
    dataset = json.loads(SRC.read_text(encoding="utf-8"))
    questions = dataset["questions"]
    by_id = {q["id"]: q for q in questions}

    conn = duckdb.connect(str(DB), read_only=True)

    def ok(sql: str) -> tuple[bool, int, str]:
        try:
            rows = conn.execute(sql).fetchall()
            return True, len(rows), ""
        except Exception as exc:
            return False, 0, str(exc)[:100]

    pairs: list[dict] = []

    def add(pid: str, base_id: str, followup: str, gold: str, kind: str, note: str) -> bool:
        good, n, err = ok(gold)
        if not good or n == 0:
            print(f"  [跳过] {pid} {base_id} {kind}  行数={n} 错误={err}")
            return False
        base = by_id[base_id]
        pairs.append(
            {
                "id": pid,
                "base_id": base_id,
                "base_question": base["question"],
                "base_gold_sql": base["gold_sql"].strip(),
                "followup": followup,
                "gold_sql": gold.strip(),
                "gold_rows": n,
                "kind": kind,
                "tags": [kind, base["difficulty"]],
                "note": note,
            }
        )
        return True

    # ---- T1 换年份：挑 time filter 是单一区间的题 ----
    t1_ids = [
        q["id"]
        for q in questions
        if "TIMESTAMP '2025-01-01'" in q["gold_sql"]
        and TIME_RANGE.search(q["gold_sql"])
        and "EXTRACT(year" not in q["gold_sql"]  # 同比题不能简单换年份
        and "2024-01-01" not in q["gold_sql"]
    ][:10]

    for idx, qid in enumerate(t1_ids, 1):
        gold = swap_year(by_id[qid]["gold_sql"])
        add(
            f"mt{idx:02d}",
            qid,
            "那换成 2024 年呢？",
            gold,
            "swap_year",
            "把时间范围整体平移到上一年",
        )

    # ---- T2 不限时间：删掉时间过滤 ----
    # 同比题（gold 里同时出现 2024 和 2025 两个年度）不能这么删：
    # 删掉时间范围之后"同比"就没有基准了，标答会变成一句没有意义的话。
    # 这是第一版数据集的一个 bug（mt16），修掉后重跑。
    t2_ids = [
        q["id"]
        for q in questions
        if TIME_RANGE.search(q["gold_sql"])
        and "EXTRACT(year" not in q["gold_sql"]
        and "2024-01-01" not in q["gold_sql"]
        and "CASE WHEN" not in q["gold_sql"].upper()
    ][:6]

    for offset, qid in enumerate(t2_ids, len(pairs) + 1):
        gold = drop_time(by_id[qid]["gold_sql"])
        if gold is None:
            continue
        add(
            f"mt{offset:02d}",
            qid,
            "那不限时间呢？把库里的全部时间都算上。",
            gold,
            "drop_time",
            "去掉时间范围，口径不变",
        )

    # ---- T3 换 TOP-N ----
    t3 = [(q["id"], 5, 3) for q in questions if re.search(r"LIMIT 5\s*$", q["gold_sql"].strip())]
    t3 += [(q["id"], 10, 5) for q in questions if re.search(r"LIMIT 10\s*$", q["gold_sql"].strip())]

    for offset, (qid, old_n, new_n) in enumerate(t3[:4], len(pairs) + 1):
        gold = swap_limit(by_id[qid]["gold_sql"], new_n)
        if gold is None:
            continue
        add(
            f"mt{offset:02d}",
            qid,
            f"那只要前 {new_n} 名呢？",
            gold,
            "swap_limit",
            f"排名范围从前 {old_n} 收窄到前 {new_n}",
        )

    conn.close()

    payload = {
        "version": "mt1.0",
        "generated_by": "scripts/build_multiturn.py",
        "note": "三种机械变换：换年份 / 不限时间 / 换 TOP-N。每条追加题的标答都由基础题标答变换得到。",
        "pairs": pairs,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    from collections import Counter

    print(f"\n生成 {len(pairs)} 条，写入 {OUT}")
    for kind, n in Counter(p["kind"] for p in pairs).items():
        print(f"  {kind:<12} {n}")


if __name__ == "__main__":
    main()
