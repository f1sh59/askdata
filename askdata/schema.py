"""把数据库结构渲染成给模型看的"语义层"。

P1 走的是最朴素的路：7 张表、全部字段、枚举值、每表两行样例，一次性塞进 prompt。
它的价值不只在"能跑"，更在于它是 P2（Schema Linking）的对照实验组 ——
只有先知道"全量注入能到多少分"，才谈得上"召回几张表有没有掉分"。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .db import Sandbox

TABLE_COMMENTS = {
    "channels": "渠道维表（一行 = 一个销售渠道）",
    "users": "用户维表（一行 = 一个注册用户，is_deleted=TRUE 表示已注销）",
    "products": "商品维表（一行 = 一个 SKU，cost 是成本价）",
    "orders": "订单头（一行 = 一笔订单，包含已取消/待支付/已退款订单）",
    "order_items": "订单明细（一行 = 订单里的一件商品，item_amount = unit_price × quantity）",
    "payments": "支付流水（一行 = 一次支付尝试，pay_status 可能是成功/失败/退款）",
    "refunds": "退款单（一行 = 一次退款申请，status 可能是处理中/已完成/已拒绝）",
}

COLUMN_COMMENTS = {
    "channels.channel_type": "平台电商 / 内容电商 / 自营",
    "users.city": "城市，存在 NULL",
    "users.province": "省份，存在 NULL",
    "users.register_date": "注册日期（DATE 类型）",
    "users.member_level": "会员等级，存在 NULL",
    "users.is_deleted": "软删除标记，为 TRUE 的用户历史订单仍然有效",
    "products.category": "商品品类（一级）",
    "products.sub_category": "二级类目",
    "products.list_price": "商品标价",
    "products.cost": "成本价（每件）",
    "products.is_online": "是否在售，FALSE 表示已下架但历史订单里仍有",
    "orders.order_time": "下单时间（TIMESTAMP 类型）",
    "orders.status": "订单状态：待支付/已支付/已发货/已完成/已取消/已退款",
    "orders.total_amount": "折扣前商品金额合计",
    "orders.discount_amount": "优惠金额",
    "orders.pay_amount": "实付金额 = total_amount - discount_amount",
    "orders.coupon_code": "使用的优惠券编码，可为 NULL",
    "payments.pay_status": "支付状态：成功/失败/退款",
    "refunds.status": "退款状态：处理中/已完成/已拒绝",
    "refunds.refund_time": "退款发生时间，按此字段归月",
}

BUSINESS_RULES = """\
1. 有效订单 = status IN ('已支付', '已发货', '已完成')；「已取消」「待支付」「已退款」都不算。
2. 销售额 / GMV = 有效订单的 pay_amount 之和，不是 total_amount。
3. 退款金额 = refunds 表中 status = '已完成' 的 refund_amount；「处理中」「已拒绝」不算。
4. 2025 年 = order_time >= TIMESTAMP '2025-01-01' AND order_time < TIMESTAMP '2026-01-01'。
5. 退款按 refund_time 归月，订单按下单时间 order_time 归月。
6. 用户表存在 NULL（city / province / member_level），分组统计时注意不要丢行。
7. order_items 与 orders 的订单头金额可能有 2% 的不一致，按明细统计时以 item_amount 为准。
8. 品类 / 商品维度的销售额用明细 order_items.item_amount 汇总，不要用订单的 pay_amount——
   多明细订单直接 JOIN 后 SUM(pay_amount) 会按明细行数重复累加（join 扇出）。
9. 毛利率 =（销售额 − 成本）/ 销售额 × 100；成本 = products.cost × order_items.quantity。
   products.cost 是每件成本价，这个字段是存在的，不需要再向用户确认成本来源。
10. 按省份 / 城市统计时排除 user.province / city 为 NULL 的行，避免出现空名分组。"""

ENUM_MAX_DISTINCT = 30

# 这些表的行数少，样例多给几行没坏处；订单/明细只给 2 行
SAMPLE_ROWS = 2


@dataclass(slots=True)
class ColumnInfo:
    name: str
    data_type: str
    note: str = ""


@dataclass(slots=True)
class TableInfo:
    name: str
    comment: str
    columns: list[ColumnInfo] = field(default_factory=list)
    enums: dict[str, list[str]] = field(default_factory=dict)
    samples: list[tuple] = field(default_factory=list)
    sample_columns: list[str] = field(default_factory=list)


def _scalar(value: object) -> str:
    if value is None:
        return "NULL"
    text = str(value)
    return text if len(text) <= 40 else text[:37] + "..."


def introspect(sandbox: Sandbox) -> list[TableInfo]:
    tables_result = sandbox.run(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'main' ORDER BY table_name"
    )
    names = [row[0] for row in tables_result.rows]

    tables: list[TableInfo] = []
    for name in names:
        cols = sandbox.run(
            "SELECT column_name, data_type FROM information_schema.columns "
            f"WHERE table_name = '{name}' ORDER BY ordinal_position"
        )
        table = TableInfo(name=name, comment=TABLE_COMMENTS.get(name, ""))
        for col_name, data_type in cols.rows:
            table.columns.append(
                ColumnInfo(col_name, data_type, COLUMN_COMMENTS.get(f"{name}.{col_name}", ""))
            )

        for column in table.columns:
            # 只给字符串列列枚举值：INTEGER 列（channel_id、quantity）的取值对写 SQL 没帮助，只会污染 prompt
            if "VARCHAR" not in column.data_type.upper():
                continue
            probe = sandbox.run(
                f'SELECT COUNT(DISTINCT "{column.name}") FROM "{name}"'
            )
            if not probe.ok or not probe.rows:
                continue
            distinct = probe.rows[0][0] or 0
            if 1 < distinct <= ENUM_MAX_DISTINCT:
                values = sandbox.run(
                    f'SELECT "{column.name}", COUNT(*) AS c FROM "{name}" '
                    f'WHERE "{column.name}" IS NOT NULL GROUP BY 1 ORDER BY c DESC, 1'
                )
                if values.ok:
                    table.enums[column.name] = [str(row[0]) for row in values.rows]

        samples = sandbox.run(f'SELECT * FROM "{name}" ORDER BY 1 LIMIT {SAMPLE_ROWS}')
        if samples.ok:
            table.samples = samples.rows
            table.sample_columns = samples.columns
        tables.append(table)
    return tables


def render(tables: list[TableInfo]) -> str:
    blocks: list[str] = []
    for table in tables:
        lines = [f"### {table.name}  {table.comment}", "字段："]
        for col in table.columns:
            suffix = f"  -- {col.note}" if col.note else ""
            lines.append(f"- {col.name} {col.data_type}{suffix}")
        if table.enums:
            lines.append("枚举值：")
            for column, values in table.enums.items():
                lines.append(f"- {column}: {' / '.join(values)}")
        sample_cols = getattr(table, "sample_columns", [])
        if table.samples and sample_cols:
            lines.append("样例数据：")
            for row in table.samples:
                cells = ", ".join(
                    f"{c}={_scalar(v)}" for c, v in zip(sample_cols, row, strict=True)
                )
                lines.append(f"- {cells}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def describe(sandbox: Sandbox) -> str:
    return render(introspect(sandbox))
