"""一次性迁移脚本：把 30 题的 v1.1 扩成 50 题的 v2.0。

⚠️ 已废弃，不要重跑。v2.1 / v2.2 的口径修订是直接改 `eval/questions.json` 的，
本脚本的输出已与现行数据集不一致，保留它只为记录 v2.0 是怎么扩出来的。

扩题原则（吸取 v1.1 的教训）：
* 每题都必须写清返回几列、单位是百分比还是小数、分母是谁；
* 新增题集中在三类：多表深度关联、隐式业务口径、时间对比；
* 旧 30 题打上 cohort=core30，新 20 题打 cohort=ext20，
  这样在 50 题上跑分时，仍然能和之前的 90% 直接对比。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "eval" / "questions_v1_1.json"
DST = ROOT / "eval" / "questions_v2_0.json"

VALID = "status IN ('已支付', '已发货', '已完成')"
Y25_O = "order_time >= TIMESTAMP '2025-01-01' AND order_time < TIMESTAMP '2026-01-01'"
Y24_O = "order_time >= TIMESTAMP '2024-01-01' AND order_time < TIMESTAMP '2025-01-01'"
Y25_R = "refund_time >= TIMESTAMP '2025-01-01' AND refund_time < TIMESTAMP '2026-01-01'"


def q(qid, difficulty, tags, question, gold_sql, note):
    return {
        "id": qid,
        "difficulty": difficulty,
        "tags": tags,
        "question": question,
        "gold_sql": " ".join(gold_sql.split()),
        "note": note,
        "cohort": "ext20",
    }


EXT20 = [
    # ---------------- medium：多表关联（3~4 张表） ----------------
    q(
        "m13", "medium", ["join", "ratio", "left_join", "business_rule"],
        "2025 年各渠道的销售额、退款金额和退款率分别是多少？（退款率 = 已完成退款金额 / 销售额 × 100，返回四列：渠道、销售额、退款金额、退款率）",
        f"""
        WITH gmv AS (
            SELECT channel_id, SUM(pay_amount) AS amt FROM orders
            WHERE {VALID} AND {Y25_O} GROUP BY channel_id
        ), rf AS (
            SELECT o.channel_id, SUM(r.refund_amount) AS amt
            FROM refunds r JOIN orders o ON o.order_id = r.order_id
            WHERE r.status = '已完成' AND r.{Y25_R} GROUP BY o.channel_id
        )
        SELECT c.channel_name, ROUND(g.amt, 2), ROUND(COALESCE(rf.amt, 0), 2),
               ROUND(COALESCE(rf.amt, 0) / g.amt * 100, 2)
        FROM gmv g JOIN channels c ON c.channel_id = g.channel_id
        LEFT JOIN rf ON rf.channel_id = g.channel_id
        ORDER BY 2 DESC
        """,
        "退款额按退款时间归 2025；没有退款的渠道要补 0（LEFT JOIN）",
    ),
    q(
        "m14", "medium", ["join", "group", "null_handling"],
        "2025 年各省份的销售额和下单用户数分别是多少？（返回三列：省份、销售额、下单用户数，忽略省份为空的用户）",
        f"""
        SELECT u.province, ROUND(SUM(o.pay_amount), 2) AS gmv, COUNT(DISTINCT o.user_id) AS user_cnt
        FROM orders o JOIN users u ON u.user_id = o.user_id
        WHERE o.{VALID} AND o.{Y25_O} AND u.province IS NOT NULL
        GROUP BY u.province ORDER BY gmv DESC
        """,
        "province 有 NULL，必须显式排除",
    ),
    q(
        "m15", "medium", ["join", "group", "conditional_agg"],
        "2025 年各品类中有销量的在售 SKU 数、销量件数和销售额分别是多少？（返回四列：品类、在售 SKU 数、销量件数、销售额）",
        f"""
        SELECT p.category,
               COUNT(DISTINCT CASE WHEN p.is_online THEN p.product_id END) AS online_sku_cnt,
               SUM(oi.quantity) AS units,
               ROUND(SUM(oi.item_amount), 2) AS amt
        FROM order_items oi
        JOIN orders o ON o.order_id = oi.order_id
        JOIN products p ON p.product_id = oi.product_id
        WHERE o.{VALID} AND o.{Y25_O}
        GROUP BY p.category ORDER BY amt DESC
        """,
        "SKU 数只数 is_online=TRUE 的，且限定 2025 年有销量",
    ),
    q(
        "m16", "medium", ["subquery", "group", "null_handling"],
        "2025 年各会员等级的复购用户数分别是多少？（复购用户 = 2025 年有效订单不少于 2 笔的用户；返回两列：会员等级、复购用户数）",
        f"""
        WITH repeat_users AS (
            SELECT user_id FROM orders WHERE {VALID} AND {Y25_O}
            GROUP BY user_id HAVING COUNT(*) >= 2
        )
        SELECT COALESCE(u.member_level, '未知') AS member_level, COUNT(*) AS repeat_user_cnt
        FROM repeat_users r JOIN users u ON u.user_id = r.user_id
        GROUP BY 1 ORDER BY 2 DESC
        """,
        "member_level 有 NULL，归「未知」",
    ),
    q(
        "m17", "medium", ["payment", "group", "aggregate"],
        "2025 年各支付方式的成功支付笔数、金额和平均每笔金额分别是多少？（返回四列：支付方式、笔数、金额、平均每笔金额）",
        f"""
        SELECT pay_method, COUNT(*) AS cnt, ROUND(SUM(pay_amount), 2) AS amt, ROUND(AVG(pay_amount), 2) AS avg_amt
        FROM payments
        WHERE pay_status = '成功' AND pay_time >= TIMESTAMP '2025-01-01' AND pay_time < TIMESTAMP '2026-01-01'
        GROUP BY pay_method ORDER BY amt DESC
        """,
        "按支付流水口径，只算成功",
    ),
    q(
        "m18", "medium", ["join", "group", "aggregate"],
        "2025 年各渠道的有效订单数和客单价分别是多少？（返回三列：渠道、有效订单数、客单价）",
        f"""
        SELECT c.channel_name, COUNT(*) AS order_cnt, ROUND(AVG(o.pay_amount), 2) AS aov
        FROM orders o JOIN channels c ON c.channel_id = o.channel_id
        WHERE o.{VALID} AND o.{Y25_O}
        GROUP BY c.channel_name ORDER BY aov DESC
        """,
        "客单价 = AVG(pay_amount)",
    ),
    q(
        "m19", "medium", ["join", "group", "top_n"],
        "2025 年销售额排名前 10 的商品是哪些？请给出商品名称、品类、销量件数和销售额。",
        f"""
        SELECT p.product_name, p.category, SUM(oi.quantity) AS units, ROUND(SUM(oi.item_amount), 2) AS amt
        FROM order_items oi
        JOIN orders o ON o.order_id = oi.order_id
        JOIN products p ON p.product_id = oi.product_id
        WHERE o.{VALID} AND o.{Y25_O}
        GROUP BY p.product_name, p.category
        ORDER BY amt DESC, p.product_name LIMIT 10
        """,
        "返回四列；同额时用商品名做次序保证稳定",
    ),
    q(
        "m20", "medium", ["subquery", "join", "null_handling"],
        "2025 年各城市的新客数分别是多少？（新客 = 该用户第一次下有效订单发生在 2025 年；返回两列：城市、新客数，忽略城市为空）",
        f"""
        WITH first_order AS (
            SELECT user_id, MIN(order_time) AS first_time FROM orders WHERE {VALID} GROUP BY user_id
        )
        SELECT u.city, COUNT(*) AS new_user_cnt
        FROM first_order f JOIN users u ON u.user_id = f.user_id
        WHERE f.first_time >= TIMESTAMP '2025-01-01' AND f.first_time < TIMESTAMP '2026-01-01'
          AND u.city IS NOT NULL
        GROUP BY u.city ORDER BY 2 DESC
        """,
        "先在全量历史里算首单时间，再按 2025 过滤",
    ),
    # ---------------- hard：时间对比 / 窗口 / 隐式口径 ----------------
    q(
        "h09", "hard", ["conditional_agg", "join", "yoy", "null_handling"],
        "2025 年各省份的销售额相比 2024 年同比增长了多少？请给出省份、2024 销售额、2025 销售额和增长率（百分比），忽略省份为空的用户。",
        f"""
        SELECT u.province,
               ROUND(SUM(CASE WHEN EXTRACT(year FROM o.order_time) = 2024 THEN o.pay_amount ELSE 0 END), 2) AS amt_2024,
               ROUND(SUM(CASE WHEN EXTRACT(year FROM o.order_time) = 2025 THEN o.pay_amount ELSE 0 END), 2) AS amt_2025,
               ROUND((SUM(CASE WHEN EXTRACT(year FROM o.order_time) = 2025 THEN o.pay_amount ELSE 0 END)
                    - SUM(CASE WHEN EXTRACT(year FROM o.order_time) = 2024 THEN o.pay_amount ELSE 0 END))
                    / SUM(CASE WHEN EXTRACT(year FROM o.order_time) = 2024 THEN o.pay_amount ELSE 0 END) * 100, 2) AS yoy_pct
        FROM orders o JOIN users u ON u.user_id = o.user_id
        WHERE o.{VALID} AND (o.{Y24_O} OR o.{Y25_O}) AND u.province IS NOT NULL
        GROUP BY u.province ORDER BY amt_2025 DESC
        """,
        "四个字段顺序固定：省份、2024、2025、增长率",
    ),
    q(
        "h10", "hard", ["window", "join", "time_bucket"],
        "2025 年各品类每月的销售额，以及该月及其前两个月（共 3 个月）的移动平均销售额分别是多少？（返回四列：品类、月份、当月销售额、3 个月移动平均；按品类、月份排序）",
        f"""
        WITH m AS (
            SELECT p.category, strftime(o.order_time, '%Y-%m') AS ym, SUM(oi.item_amount) AS amt
            FROM order_items oi
            JOIN orders o ON o.order_id = oi.order_id
            JOIN products p ON p.product_id = oi.product_id
            WHERE o.{VALID} AND o.{Y25_O}
            GROUP BY 1, 2
        )
        SELECT category, ym, ROUND(amt, 2) AS amt,
               ROUND(AVG(amt) OVER (PARTITION BY category ORDER BY ym ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2) AS ma3
        FROM m ORDER BY category, ym
        """,
        "窗口按品类分区、按月份排序，取当前行及前两行",
    ),
    q(
        "h11", "hard", ["subquery", "ratio", "business_rule"],
        "2025 年新客的销售额占 2025 年总销售额的百分之多少？（新客 = 该用户第一次下有效订单发生在 2025 年；返回一个百分比数值）",
        f"""
        WITH first_order AS (
            SELECT user_id, MIN(order_time) AS first_time FROM orders WHERE {VALID} GROUP BY user_id
        ), new_users AS (
            SELECT user_id FROM first_order
            WHERE first_time >= TIMESTAMP '2025-01-01' AND first_time < TIMESTAMP '2026-01-01'
        )
        SELECT ROUND(
            (SELECT SUM(o.pay_amount) FROM orders o JOIN new_users n ON n.user_id = o.user_id
             WHERE o.{VALID} AND o.{Y25_O})
            / (SELECT SUM(pay_amount) FROM orders WHERE {VALID} AND {Y25_O}) * 100, 2) AS new_user_gmv_pct
        """,
        "分子分母都只算 2025 年有效订单",
    ),
    q(
        "h12", "hard", ["ratio", "group", "having", "business_rule"],
        "2025 年的复购率是多少？（复购率 = 有效订单不少于 2 笔的用户数 / 下单用户数 × 100，返回一个百分比数值）",
        f"""
        WITH u AS (
            SELECT user_id, COUNT(*) AS c FROM orders WHERE {VALID} AND {Y25_O} GROUP BY user_id
        )
        SELECT ROUND(COUNT(*) FILTER (WHERE c >= 2) * 100.0 / COUNT(*), 2) AS repeat_rate_pct FROM u
        """,
        "分母是 2025 年有下单的用户数，不是全部注册用户",
    ),
    q(
        "h13", "hard", ["join", "subquery", "ratio", "order_by_limit"],
        "2025 年退款率最高的前 3 个商品品类是哪些？（退款率 = 该品类分摊到的退款金额 / 该品类销售额 × 100；退款金额按订单内各品类的明细金额占比分摊；返回四列：品类、销售额、退款金额、退款率）",
        f"""
        WITH item_cat AS (
            SELECT oi.order_id, p.category, SUM(oi.item_amount) AS cat_amt
            FROM order_items oi JOIN products p ON p.product_id = oi.product_id
            GROUP BY 1, 2
        ), order_amt AS (
            SELECT order_id, SUM(cat_amt) AS amt FROM item_cat GROUP BY order_id
        ), alloc AS (
            SELECT ic.category, r.refund_amount * ic.cat_amt / oa.amt AS refund_amt
            FROM refunds r
            JOIN item_cat ic ON ic.order_id = r.order_id
            JOIN order_amt oa ON oa.order_id = r.order_id
            WHERE r.status = '已完成' AND r.{Y25_R}
        ), rf AS (
            SELECT category, SUM(refund_amt) AS amt FROM alloc GROUP BY 1
        ), gmv AS (
            SELECT p.category, SUM(oi.item_amount) AS amt
            FROM order_items oi
            JOIN orders o ON o.order_id = oi.order_id
            JOIN products p ON p.product_id = oi.product_id
            WHERE o.{VALID} AND o.{Y25_O}
            GROUP BY p.category
        )
        SELECT g.category, ROUND(g.amt, 2), ROUND(COALESCE(rf.amt, 0), 2),
               ROUND(COALESCE(rf.amt, 0) / g.amt * 100, 2) AS refund_rate_pct
        FROM gmv g LEFT JOIN rf ON rf.category = g.category
        ORDER BY refund_rate_pct DESC LIMIT 3
        """,
        "多品类订单的退款要按明细金额占比分摊，不能整单归给一个品类",
    ),
    q(
        "h14", "hard", ["window", "self_join", "rank"],
        "各渠道 2025 年的销售额排名与 2024 年相比分别是第几名？（返回三列：渠道、2024 年排名、2025 年排名，按 2025 年排名升序）",
        f"""
        WITH y AS (
            SELECT c.channel_name, EXTRACT(year FROM o.order_time) AS yr, SUM(o.pay_amount) AS amt
            FROM orders o JOIN channels c ON c.channel_id = o.channel_id
            WHERE o.{VALID} AND (o.{Y24_O} OR o.{Y25_O})
            GROUP BY 1, 2
        ), r AS (
            SELECT channel_name, yr, amt, RANK() OVER (PARTITION BY yr ORDER BY amt DESC) AS rk FROM y
        )
        SELECT a.channel_name, a.rk AS rank_2024, b.rk AS rank_2025
        FROM r a JOIN r b ON a.channel_name = b.channel_name
        WHERE a.yr = 2024 AND b.yr = 2025
        ORDER BY rank_2025
        """,
        "排名要在各年内部各自排，不能跨年一起排",
    ),
    q(
        "h15", "hard", ["window", "subquery", "aggregate"],
        "2025 年首次下有效订单的用户，从首单到第二单平均相隔多少天？（只算 2025 年首单且至少下过两单的用户；返回一个天数，保留 1 位小数）",
        f"""
        WITH ranked AS (
            SELECT user_id, order_time, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY order_time) AS rn
            FROM orders WHERE {VALID}
        ), pairs AS (
            SELECT user_id,
                   MAX(CASE WHEN rn = 1 THEN order_time END) AS t1,
                   MAX(CASE WHEN rn = 2 THEN order_time END) AS t2
            FROM ranked GROUP BY user_id
        )
        SELECT ROUND(AVG(date_diff('day', t1, t2)), 1) AS avg_gap_days
        FROM pairs
        WHERE t2 IS NOT NULL AND t1 >= TIMESTAMP '2025-01-01' AND t1 < TIMESTAMP '2026-01-01'
        """,
        "首单和第二单要从全量历史里取，不能先按 2025 过滤",
    ),
    q(
        "h16", "hard", ["join", "ratio", "null_handling", "business_rule"],
        "2025 年各会员等级的人均订单数分别是多少？（人均订单数 = 有效订单数 / 该等级 2025 年有下单的用户数；返回四列：会员等级、订单数、用户数、人均订单数，会员等级缺失归为「未知」）",
        f"""
        SELECT COALESCE(u.member_level, '未知') AS member_level,
               COUNT(*) AS order_cnt,
               COUNT(DISTINCT o.user_id) AS user_cnt,
               ROUND(COUNT(*) * 1.0 / COUNT(DISTINCT o.user_id), 2) AS orders_per_user
        FROM orders o JOIN users u ON u.user_id = o.user_id
        WHERE o.{VALID} AND o.{Y25_O}
        GROUP BY 1 ORDER BY orders_per_user DESC
        """,
        "分母是「2025 年有下单的用户数」，不是该等级全部注册用户",
    ),
    q(
        "h17", "hard", ["window", "left_join", "time_bucket", "ratio"],
        "2025 年每月的累计销售额和累计退款率分别是多少？（累计退款率 = 年初到当月的已完成退款金额 / 年初到当月的销售额 × 100；返回四列：月份、累计销售额、累计退款金额、累计退款率）",
        f"""
        WITH g AS (
            SELECT strftime(order_time, '%Y-%m') AS ym, SUM(pay_amount) AS amt
            FROM orders WHERE {VALID} AND {Y25_O} GROUP BY 1
        ), r AS (
            SELECT strftime(refund_time, '%Y-%m') AS ym, SUM(refund_amount) AS amt
            FROM refunds WHERE status = '已完成' AND {Y25_R} GROUP BY 1
        )
        SELECT g.ym,
               ROUND(SUM(g.amt) OVER (ORDER BY g.ym), 2) AS cum_gmv,
               ROUND(SUM(COALESCE(r.amt, 0)) OVER (ORDER BY g.ym), 2) AS cum_refund,
               ROUND(SUM(COALESCE(r.amt, 0)) OVER (ORDER BY g.ym) / SUM(g.amt) OVER (ORDER BY g.ym) * 100, 2) AS cum_refund_rate
        FROM g LEFT JOIN r ON r.ym = g.ym
        ORDER BY g.ym
        """,
        "累计是年初到当月，不是滚动窗口；无退款月份补 0",
    ),
    q(
        "h18", "hard", ["conditional_agg", "join", "yoy", "top_n"],
        "2025 年销售额同比增幅最高的前 3 个商品品类是哪些？（返回四列：品类、2024 销售额、2025 销售额、同比增幅百分比）",
        f"""
        WITH y AS (
            SELECT p.category, EXTRACT(year FROM o.order_time) AS yr, SUM(oi.item_amount) AS amt
            FROM order_items oi
            JOIN orders o ON o.order_id = oi.order_id
            JOIN products p ON p.product_id = oi.product_id
            WHERE o.{VALID} AND (o.{Y24_O} OR o.{Y25_O})
            GROUP BY 1, 2
        ), piv AS (
            SELECT category,
                   SUM(CASE WHEN yr = 2024 THEN amt ELSE 0 END) AS amt_2024,
                   SUM(CASE WHEN yr = 2025 THEN amt ELSE 0 END) AS amt_2025
            FROM y GROUP BY 1
        )
        SELECT category, ROUND(amt_2024, 2), ROUND(amt_2025, 2),
               ROUND((amt_2025 - amt_2024) / amt_2024 * 100, 2) AS yoy_pct
        FROM piv ORDER BY yoy_pct DESC LIMIT 3
        """,
        "同比按品类各自计算，再取增幅前三",
    ),
    q(
        "h19", "hard", ["window", "join", "ratio", "group"],
        "2025 年各渠道的已完成退款中，各种退款原因各有多少笔、占该渠道退款的百分之多少？（返回三列：渠道、退款原因、笔数、占比）",
        f"""
        SELECT c.channel_name, r.reason, COUNT(*) AS cnt,
               ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (PARTITION BY c.channel_name), 2) AS pct
        FROM refunds r
        JOIN orders o ON o.order_id = r.order_id
        JOIN channels c ON c.channel_id = o.channel_id
        WHERE r.status = '已完成' AND r.{Y25_R}
        GROUP BY 1, 2
        ORDER BY c.channel_name, cnt DESC
        """,
        "占比的分母是同一渠道内的退款笔数",
    ),
    q(
        "h20", "hard", ["window", "join", "pivot", "top_n"],
        "2025 年各品类销量最高的商品，与前一名之外的「第二名」差距是多少？请给出品类、第一名商品、第一名销售额、第二名销售额和两者差额。",
        f"""
        WITH s AS (
            SELECT p.category, p.product_name, SUM(oi.item_amount) AS amt
            FROM order_items oi
            JOIN orders o ON o.order_id = oi.order_id
            JOIN products p ON p.product_id = oi.product_id
            WHERE o.{VALID} AND o.{Y25_O}
            GROUP BY 1, 2
        ), r AS (
            SELECT category, product_name, amt,
                   ROW_NUMBER() OVER (PARTITION BY category ORDER BY amt DESC, product_name) AS rn
            FROM s
        )
        SELECT a.category, a.product_name AS top1_product, ROUND(a.amt, 2) AS top1_amt,
               ROUND(COALESCE(b.amt, 0), 2) AS top2_amt, ROUND(a.amt - COALESCE(b.amt, 0), 2) AS gap
        FROM r a LEFT JOIN r b ON b.category = a.category AND b.rn = 2
        WHERE a.rn = 1
        ORDER BY gap DESC
        """,
        "组内取前两名再自连接，不是全局取前二",
    ),
]


def main() -> None:
    data = json.loads(SRC.read_text(encoding="utf-8"))
    for item in data["questions"]:
        item["cohort"] = "core30"
    data["questions"].extend(EXT20)
    data["version"] = "v2.0"
    data["created_at"] = "2026-11-18"
    data["description"] = (
        "askdata 中文问数评测集：50 题（core30 + ext20）。ext20 侧重重表关联、隐式业务口径与时间对比，"
        "用来在 90% 附近重新拉开区分度。"
    )
    data["cohorts"] = {
        "core30": "v1.1 的 30 道题，与前期 83~90% 的跑分直接可比",
        "ext20": "v2.0 新增的 20 道难题",
    }
    DST.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {DST} total={len(data['questions'])}")


if __name__ == "__main__":
    main()
