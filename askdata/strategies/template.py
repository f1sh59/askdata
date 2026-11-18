"""无 LLM 的关键词模板 baseline —— 这是评测的地板，不是上限。

它的存在只有一个目的：给后面每一个 LLM 版本提供一个稳定的对照点。
一个只会做关键词匹配的规则引擎，在这份 30 题评测集上大概只能答对
最直白的那几道题，剩下全部 no_sql。真人看到这个分数，才知道 LLM 到底带来了多少。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Question

VALID = "status IN ('已支付', '已发货', '已完成')"
Y2025 = "order_time >= TIMESTAMP '2025-01-01' AND order_time < TIMESTAMP '2026-01-01'"


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    must_all: tuple[str, ...]
    sql: str
    must_any: tuple[str, ...] = ()
    forbid: tuple[str, ...] = ()

    def matches(self, text: str) -> bool:
        if any(word in text for word in self.forbid):
            return False
        if not all(word in text for word in self.must_all):
            return False
        return not self.must_any or any(word in text for word in self.must_any)


RULES: tuple[Rule, ...] = (
    Rule(
        "count_orders_2025",
        must_all=("多少笔订单",),
        sql=f"SELECT COUNT(*) AS order_cnt FROM orders WHERE {Y2025}",
    ),
    Rule(
        "refund_sum_2025",
        must_all=("退款金额",),
        forbid=("每月", "品类", "渠道", "占比", "率"),
        sql=(
            "SELECT ROUND(SUM(refund_amount), 2) AS refund_amt FROM refunds "
            "WHERE status = '已完成' AND refund_time >= TIMESTAMP '2025-01-01' "
            "AND refund_time < TIMESTAMP '2026-01-01'"
        ),
    ),
    Rule(
        "gmv_2025",
        must_all=("销售额",),
        forbid=("每月", "品类", "渠道", "商品", "排名", "前 5", "前5", "同比", "增长", "平均"),
        sql=f"SELECT ROUND(SUM(pay_amount), 2) AS gmv FROM orders WHERE {VALID} AND {Y2025}",
    ),
    Rule(
        "online_product_count",
        must_all=("商品",),
        must_any=("在售", "上架"),
        sql="SELECT COUNT(*) AS product_cnt FROM products WHERE is_online = TRUE",
    ),
    Rule(
        "active_user_count",
        must_all=("用户",),
        must_any=("未注销",),
        sql="SELECT COUNT(*) AS user_cnt FROM users WHERE is_deleted = FALSE",
    ),
    Rule(
        "category_count",
        must_all=("品类",),
        must_any=("几个", "多少种"),
        sql="SELECT COUNT(DISTINCT category) AS category_cnt FROM products",
    ),
    Rule(
        "payment_by_method",
        must_all=("支付方式",),
        forbid=("占比", "每月"),
        sql=(
            "SELECT pay_method, ROUND(SUM(pay_amount), 2) AS amt FROM payments "
            "WHERE pay_status = '成功' GROUP BY pay_method ORDER BY amt DESC"
        ),
    ),
    Rule(
        "average_order_value",
        must_all=("平均订单金额",),
        sql=f"SELECT ROUND(AVG(pay_amount), 2) AS aov FROM orders WHERE {VALID} AND {Y2025}",
    ),
    Rule(
        "distinct_buyers",
        must_all=("不同用户",),
        sql=f"SELECT COUNT(DISTINCT user_id) AS user_cnt FROM orders WHERE {VALID} AND {Y2025}",
    ),
)


class TemplateStrategy:
    name = "template"

    def generate(self, question: Question) -> str | None:
        text = question.question
        for rule in RULES:
            if rule.matches(text):
                return rule.sql
        return None
