"""确定性生成 askdata 数据集。

固定随机种子，任何人跑出来的库完全一致 —— 评测集（eval/questions_v1.json）
的 gold SQL 就是对着这份数据写的，数据一变所有指标都不可比。

用法：
    python scripts/build_db.py            # 重建 data/askdata.duckdb
    python scripts/build_db.py --verify   # 重建并打印关键口径数值
"""

from __future__ import annotations

import random
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "data" / "schema.sql"

# 数据库路径只有一份来源：askdata.config.DB_PATH。
# 这里曾经自己写死过一份 ROOT/"data"/"askdata.duckdb"，结果是
# 设了 ASKDATA_DB 之后建库和读库会指向两个文件（并且容器里 entrypoint 的变量形同虚设）。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from askdata import config as askdata_config  # noqa: E402

DB_PATH = askdata_config.DB_PATH

SEED = 20260101
N_USERS = 600
N_PRODUCTS = 90
START_YEAR, START_MONTH, N_MONTHS = 2024, 1, 24

CENT = Decimal("0.01")


def q(value: Decimal) -> Decimal:
    return value.quantize(CENT)


CHANNELS = [
    (1, "天猫", "平台电商"),
    (2, "京东", "平台电商"),
    (3, "抖音", "内容电商"),
    (4, "官网", "自营"),
    (5, "小程序", "自营"),
]

CITIES = [
    ("杭州", "浙江"), ("上海", "上海"), ("北京", "北京"), ("深圳", "广东"),
    ("广州", "广东"), ("成都", "四川"), ("武汉", "湖北"), ("南京", "江苏"),
    ("西安", "陕西"), ("重庆", "重庆"), ("苏州", "江苏"), ("长沙", "湖南"),
]

CATALOG = {
    "数码": {"手机": (1999, 8999), "耳机": (99, 2499), "平板": (1499, 6999), "配件": (19, 399)},
    "家电": {"冰箱": (899, 6999), "洗衣机": (1299, 5999), "空调": (1599, 7999), "厨房电器": (99, 1999)},
    "服饰": {"男装": (59, 899), "女装": (69, 1299), "鞋履": (129, 1599), "配饰": (19, 399)},
    "食品": {"零食": (5, 199), "饮料": (3, 129), "生鲜": (9, 299), "粮油": (15, 399)},
    "美妆": {"护肤": (39, 1299), "彩妆": (29, 599), "个护": (9, 199), "香水": (99, 1499)},
}

STATUS_WEIGHTS = [
    ("已完成", 0.52), ("已发货", 0.14), ("已支付", 0.12),
    ("已取消", 0.13), ("待支付", 0.04), ("已退款", 0.05),
]
CHANNEL_WEIGHTS = [0.32, 0.24, 0.20, 0.12, 0.12]
SEASONAL = {1: 1.00, 2: 0.72, 3: 0.95, 4: 0.95, 5: 1.05, 6: 1.15,
            7: 0.90, 8: 0.92, 9: 1.00, 10: 1.10, 11: 1.60, 12: 1.45}
PAY_METHODS = ["支付宝", "微信", "银行卡", "花呗"]
PAY_METHOD_WEIGHTS = [0.42, 0.34, 0.16, 0.08]
REFUND_REASONS = ["七天无理由", "质量问题", "发错货", "价格保护", "物流破损"]
VALID_STATUSES = ("已支付", "已发货", "已完成")


def build_users(rng: random.Random) -> list[tuple]:
    rows = []
    for uid in range(1, N_USERS + 1):
        has_city = rng.random() > 0.08          # 8% 用户没有城市信息
        city, province = rng.choice(CITIES) if has_city else (None, None)
        # 注册时间偏向更早：老用户多，新用户少（模拟真实增长曲线）
        offset = int(rng.triangular(0, 1, 0.25) * 930)   # 0~930 天
        register = date(2023, 6, 1) + timedelta(days=offset)
        level = rng.choices(
            ["普通", "银牌", "金牌", "钻石"], weights=[0.50, 0.25, 0.18, 0.07]
        )[0]
        if rng.random() < 0.05:                  # 5% 会员等级缺失
            level = None
        rows.append((
            uid, f"用户{uid:04d}", city, province, register, level,
            rng.random() < 0.03,                 # 3% 已注销（订单依然有效）
        ))
    return rows


def build_products(rng: random.Random) -> list[tuple]:
    rows = []
    flat = [(cat, sub, lo, hi) for cat, subs in CATALOG.items() for sub, (lo, hi) in subs.items()]
    for pid in range(1, N_PRODUCTS + 1):
        cat, sub, lo, hi = flat[(pid - 1) % len(flat)]
        price = Decimal(rng.randint(lo * 100, hi * 100)) / 100
        cost = q(price * Decimal(str(round(rng.uniform(0.45, 0.75), 4))))
        launch = date(2023, 1, 1) + timedelta(days=rng.randint(0, 1000))
        rows.append((
            pid, f"{cat}{sub}{pid:03d}", cat, sub, q(price), cost, launch,
            rng.random() > 0.15,                  # 15% 已下架，但历史订单里仍有
        ))
    return rows


def build_orders(rng: random.Random, products: list[tuple]) -> tuple[list, list, list, list]:
    orders, items, payments, refunds = [], [], [], []
    item_id = payment_id = refund_id = 0

    # 用户下单倾向：前 1/3 用户贡献大部分订单（长尾分布）
    user_ids = list(range(1, N_USERS + 1))
    user_weights = [1.0 if u <= 200 else 0.6 if u <= 400 else 0.25 for u in user_ids]

    order_id = 0
    for mi in range(N_MONTHS):
        year = START_YEAR + (START_MONTH - 1 + mi) // 12
        month = (START_MONTH - 1 + mi) % 12 + 1
        growth = 1 + 0.022 * mi
        count = int(300 * SEASONAL[month] * growth * rng.uniform(0.92, 1.08))

        for _ in range(count):
            order_id += 1
            uid = rng.choices(user_ids, weights=user_weights)[0]
            ch = rng.choices([c[0] for c in CHANNELS], weights=CHANNEL_WEIGHTS)[0]
            status = rng.choices([s for s, _ in STATUS_WEIGHTS], weights=[w for _, w in STATUS_WEIGHTS])[0]
            ts = datetime(year, month, rng.randint(1, 28), rng.randint(0, 23), rng.randint(0, 59))

            n_items = rng.choices([1, 2, 3, 4], weights=[0.55, 0.28, 0.12, 0.05])[0]
            picked = rng.sample(products, n_items)

            total = Decimal("0")
            for p in picked:
                item_id += 1
                pid, _, _, _, list_price = p[0], p[1], p[2], p[3], p[4]
                unit = q(Decimal(list_price) * Decimal(str(round(rng.uniform(0.75, 1.0), 4))))
                qty = rng.randint(1, 4)
                amount = q(unit * qty)
                total += amount
                items.append((item_id, order_id, pid, qty, unit, amount))

            discount = Decimal("0")
            if rng.random() < 0.55:
                discount = q(total * Decimal(str(round(rng.uniform(0.02, 0.15), 4))))
            pay = q(total - discount)

            # 2% 订单的订单头金额与明细汇总对不上（上游系统写入不一致）
            head_total = q(total * Decimal("1.05")) if rng.random() < 0.02 else total

            coupon = None
            if discount > 0 and rng.random() < 0.7:
                coupon = f"CPN{year}{(mi % 12) + 1:02d}"

            orders.append((order_id, uid, ch, ts, status, head_total, discount, pay, coupon))

            # 支付记录：成功 / 失败 / 退款
            if status in VALID_STATUSES:
                payment_id += 1
                payments.append((
                    payment_id, order_id, ts + timedelta(seconds=rng.randint(5, 300)),
                    rng.choices(PAY_METHODS, weights=PAY_METHOD_WEIGHTS)[0], pay, "成功",
                ))
            elif status == "已退款":
                payment_id += 1
                payments.append((
                    payment_id, order_id, ts + timedelta(seconds=rng.randint(5, 300)),
                    rng.choices(PAY_METHODS, weights=PAY_METHOD_WEIGHTS)[0], pay, "退款",
                ))
            elif rng.random() < 0.75:
                payment_id += 1
                payments.append((
                    payment_id, order_id, ts + timedelta(seconds=rng.randint(5, 300)),
                    rng.choices(PAY_METHODS, weights=PAY_METHOD_WEIGHTS)[0], pay, "失败",
                ))

            # 退款记录
            if status == "已退款":
                refund_id += 1
                refunds.append((
                    refund_id, order_id, ts + timedelta(days=rng.randint(1, 20)), pay,
                    rng.choice(REFUND_REASONS), "已完成" if rng.random() < 0.9 else "处理中",
                ))
            elif status == "已完成" and rng.random() < 0.08:
                refund_id += 1
                partial = q(pay * Decimal(str(round(rng.uniform(0.2, 1.0), 4))))
                refunds.append((
                    refund_id, order_id, ts + timedelta(days=rng.randint(1, 30)), partial,
                    rng.choice(REFUND_REASONS),
                    rng.choices(["已完成", "已拒绝", "处理中"], weights=[0.5, 0.3, 0.2])[0],
                ))

    return orders, items, payments, refunds


def insert(conn: duckdb.DuckDBPyConnection, sql: str, rows: list[tuple]) -> None:
    if rows:
        conn.executemany(sql, rows)


DECOY_SEED = 7
STATUS_WORDS = ["待处理", "处理中", "已完成", "已关闭"]
TYPE_WORDS = ["类型A", "类型B", "类型C"]

# 60 张"真实存在但与本项目无关"的表：模拟真实数仓里除了主业务域之外的一堆维表、明细表、日志表。
# 其中交易域、财务域、售后域是**硬负样本**——表名和字段都在跟问题里的关键词抢词，
# 召回器如果只做字面匹配，很容易把 ods_order_ext、fin_refund_audit 当成 orders、refunds。
DECOY_TABLES: list[tuple[str, str, list[tuple[str, str]], int]] = [
    # —— 客户域 ——
    ("dim_customer", "客户主数据维表", [("customer_id", "INTEGER"), ("customer_name", "VARCHAR"), ("segment_code", "VARCHAR"), ("reg_source", "VARCHAR"), ("created_at", "DATE")], 80),
    ("dim_customer_tag", "客户标签维表", [("tag_id", "INTEGER"), ("tag_name", "VARCHAR"), ("tag_level", "VARCHAR"), ("created_at", "DATE")], 40),
    ("cust_contact_log", "客户联系记录", [("log_id", "INTEGER"), ("customer_id", "INTEGER"), ("channel_type", "VARCHAR"), ("content", "VARCHAR"), ("created_at", "DATE")], 120),
    ("cust_service_ticket", "客服工单", [("ticket_id", "INTEGER"), ("customer_id", "INTEGER"), ("ticket_type", "VARCHAR"), ("status", "VARCHAR"), ("created_at", "DATE")], 90),
    ("cust_satisfaction", "满意度调查结果", [("survey_id", "INTEGER"), ("customer_id", "INTEGER"), ("score", "INTEGER"), ("created_at", "DATE")], 70),
    ("cust_segment", "客户分群", [("segment_id", "INTEGER"), ("segment_name", "VARCHAR"), ("rule_desc", "VARCHAR"), ("created_at", "DATE")], 25),
    ("cust_lifecycle", "客户生命周期阶段", [("customer_id", "INTEGER"), ("stage", "VARCHAR"), ("entered_at", "DATE")], 100),
    ("dim_customer_addr", "客户收货地址", [("addr_id", "INTEGER"), ("customer_id", "INTEGER"), ("city", "VARCHAR"), ("detail", "VARCHAR")], 110),
    # —— 商品域 ——
    ("dim_sku_ext", "SKU 扩展属性", [("sku_id", "INTEGER"), ("attr_name", "VARCHAR"), ("attr_value", "VARCHAR")], 90),
    ("dim_brand", "品牌维表", [("brand_id", "INTEGER"), ("brand_name", "VARCHAR"), ("country", "VARCHAR")], 30),
    ("dim_category_tree", "类目树", [("node_id", "INTEGER"), ("parent_id", "INTEGER"), ("node_name", "VARCHAR"), ("node_level", "VARCHAR")], 40),
    ("prod_price_history", "商品价格变更历史", [("record_id", "INTEGER"), ("product_id", "INTEGER"), ("old_price", "DECIMAL"), ("new_price", "DECIMAL"), ("created_at", "DATE")], 100),
    ("prod_stock_snapshot", "商品库存快照", [("snapshot_id", "INTEGER"), ("product_id", "INTEGER"), ("warehouse_id", "INTEGER"), ("qty", "INTEGER"), ("created_at", "DATE")], 140),
    ("prod_supplier", "供应商主数据", [("supplier_id", "INTEGER"), ("supplier_name", "VARCHAR"), ("coop_level", "VARCHAR")], 35),
    ("prod_bom", "商品组合关系", [("bom_id", "INTEGER"), ("parent_sku", "INTEGER"), ("child_sku", "INTEGER"), ("qty", "INTEGER")], 60),
    ("prod_quality_check", "商品质检记录", [("check_id", "INTEGER"), ("product_id", "INTEGER"), ("result", "VARCHAR"), ("created_at", "DATE")], 55),
    # —— 交易域（硬负样本）——
    ("ods_order_ext", "订单扩展信息（ODS）", [("order_no", "VARCHAR"), ("ext_key", "VARCHAR"), ("ext_value", "VARCHAR"), ("created_at", "DATE")], 120),
    ("ods_order_log", "订单变更日志", [("log_id", "INTEGER"), ("order_no", "VARCHAR"), ("action", "VARCHAR"), ("created_at", "DATE")], 150),
    ("ods_order_raw", "订单原始层", [("raw_id", "INTEGER"), ("payload", "VARCHAR"), ("ingest_time", "DATE")], 130),
    ("trade_order_split", "订单拆分记录", [("split_id", "INTEGER"), ("parent_order_no", "VARCHAR"), ("child_order_no", "VARCHAR"), ("qty", "INTEGER")], 60),
    ("trade_settle_detail", "交易结算明细", [("settle_id", "INTEGER"), ("order_no", "VARCHAR"), ("settle_amount", "DECIMAL"), ("settle_date", "DATE")], 120),
    ("trade_invoice", "发票记录", [("invoice_id", "INTEGER"), ("order_no", "VARCHAR"), ("invoice_type", "VARCHAR"), ("amount", "DECIMAL")], 70),
    ("trade_contract", "合同主表", [("contract_id", "INTEGER"), ("partner_name", "VARCHAR"), ("sign_date", "DATE")], 45),
    ("trade_audit_record", "交易审核记录", [("audit_id", "INTEGER"), ("order_no", "VARCHAR"), ("status", "VARCHAR"), ("created_at", "DATE")], 85),
    # —— 财务域（硬负样本）——
    ("fin_payment_channel", "支付渠道配置", [("channel_code", "VARCHAR"), ("channel_name", "VARCHAR"), ("fee_rate", "DECIMAL")], 12),
    ("fin_payment_recon", "支付对账明细", [("recon_id", "INTEGER"), ("trade_no", "VARCHAR"), ("amount", "DECIMAL"), ("diff_amount", "DECIMAL"), ("status", "VARCHAR")], 110),
    ("fin_settlement", "结算单", [("settle_id", "INTEGER"), ("merchant_id", "INTEGER"), ("settle_amount", "DECIMAL"), ("settle_date", "DATE")], 75),
    ("fin_fee_rate", "费率配置", [("rate_id", "INTEGER"), ("biz_type", "VARCHAR"), ("rate", "DECIMAL")], 20),
    ("fin_bill", "账单", [("bill_id", "INTEGER"), ("account_id", "INTEGER"), ("bill_amount", "DECIMAL"), ("bill_month", "VARCHAR")], 60),
    ("fin_ledger", "总账分录", [("entry_id", "INTEGER"), ("account_code", "VARCHAR"), ("debit", "DECIMAL"), ("credit", "DECIMAL")], 140),
    ("fin_tax_record", "税务记录", [("tax_id", "INTEGER"), ("tax_type", "VARCHAR"), ("tax_amount", "DECIMAL")], 40),
    ("fin_refund_audit", "退款审核记录", [("audit_id", "INTEGER"), ("refund_no", "VARCHAR"), ("auditor", "VARCHAR"), ("status", "VARCHAR")], 80),
    # —— 库存域 ——
    ("inv_warehouse", "仓库主数据", [("warehouse_id", "INTEGER"), ("warehouse_name", "VARCHAR"), ("city", "VARCHAR")], 15),
    ("inv_stock", "库存余额", [("stock_id", "INTEGER"), ("product_id", "INTEGER"), ("warehouse_id", "INTEGER"), ("qty", "INTEGER")], 150),
    ("inv_inbound", "入库单", [("inbound_id", "INTEGER"), ("product_id", "INTEGER"), ("qty", "INTEGER"), ("created_at", "DATE")], 110),
    ("inv_outbound", "出库单", [("outbound_id", "INTEGER"), ("product_id", "INTEGER"), ("qty", "INTEGER"), ("created_at", "DATE")], 110),
    ("inv_transfer", "调拨单", [("transfer_id", "INTEGER"), ("from_wh", "INTEGER"), ("to_wh", "INTEGER"), ("qty", "INTEGER")], 70),
    ("inv_count", "库存盘点", [("count_id", "INTEGER"), ("warehouse_id", "INTEGER"), ("diff_qty", "INTEGER"), ("created_at", "DATE")], 60),
    ("inv_batch", "库存批次", [("batch_id", "INTEGER"), ("product_id", "INTEGER"), ("batch_no", "VARCHAR"), ("expire_date", "DATE")], 90),
    # —— 物流域 ——
    ("logis_waybill", "运单", [("waybill_no", "VARCHAR"), ("order_no", "VARCHAR"), ("carrier_code", "VARCHAR"), ("status", "VARCHAR")], 130),
    ("logis_track", "物流轨迹", [("track_id", "INTEGER"), ("waybill_no", "VARCHAR"), ("node_desc", "VARCHAR"), ("track_time", "DATE")], 160),
    ("logis_carrier", "承运商", [("carrier_code", "VARCHAR"), ("carrier_name", "VARCHAR"), ("service_level", "VARCHAR")], 18),
    ("logis_fee", "运费明细", [("fee_id", "INTEGER"), ("waybill_no", "VARCHAR"), ("freight", "DECIMAL"), ("created_at", "DATE")], 100),
    ("logis_route", "物流路由", [("route_id", "INTEGER"), ("from_city", "VARCHAR"), ("to_city", "VARCHAR"), ("days", "INTEGER")], 40),
    ("logis_exception", "物流异常件", [("exception_id", "INTEGER"), ("waybill_no", "VARCHAR"), ("reason", "VARCHAR"), ("created_at", "DATE")], 65),
    # —— 营销域 ——
    ("mkt_campaign", "营销活动", [("campaign_id", "INTEGER"), ("campaign_name", "VARCHAR"), ("start_date", "DATE"), ("end_date", "DATE")], 30),
    ("mkt_coupon", "优惠券", [("coupon_id", "INTEGER"), ("coupon_code", "VARCHAR"), ("discount", "DECIMAL"), ("status", "VARCHAR")], 70),
    ("mkt_coupon_rule", "优惠券规则", [("rule_id", "INTEGER"), ("coupon_id", "INTEGER"), ("rule_desc", "VARCHAR")], 45),
    ("mkt_channel_cost", "渠道投放成本", [("cost_id", "INTEGER"), ("campaign_id", "INTEGER"), ("channel", "VARCHAR"), ("cost", "DECIMAL"), ("stat_date", "DATE")], 90),
    ("mkt_roi_daily", "每日投放 ROI", [("roi_id", "INTEGER"), ("campaign_id", "INTEGER"), ("gmv", "DECIMAL"), ("cost", "DECIMAL"), ("stat_date", "DATE")], 80),
    ("mkt_activity", "活动报名", [("signup_id", "INTEGER"), ("campaign_id", "INTEGER"), ("merchant_id", "INTEGER"), ("status", "VARCHAR")], 55),
    ("mkt_content", "内容素材", [("content_id", "INTEGER"), ("title", "VARCHAR"), ("content_type", "VARCHAR"), ("created_at", "DATE")], 50),
    # —— 售后域（硬负样本）——
    ("as_return_apply", "退货申请单", [("apply_id", "INTEGER"), ("order_no", "VARCHAR"), ("reason", "VARCHAR"), ("status", "VARCHAR")], 70),
    ("as_complaint", "客户投诉", [("complaint_id", "INTEGER"), ("customer_id", "INTEGER"), ("content", "VARCHAR"), ("status", "VARCHAR")], 55),
    ("as_repair_order", "维修工单", [("repair_id", "INTEGER"), ("product_id", "INTEGER"), ("fault_desc", "VARCHAR"), ("status", "VARCHAR")], 40),
    ("as_warranty", "保修记录", [("warranty_id", "INTEGER"), ("product_id", "INTEGER"), ("expire_date", "DATE")], 35),
    # —— 系统域 ——
    ("sys_dict", "数据字典", [("dict_id", "INTEGER"), ("dict_code", "VARCHAR"), ("dict_name", "VARCHAR")], 60),
    ("sys_user", "系统用户", [("user_id", "INTEGER"), ("login_name", "VARCHAR"), ("role_id", "INTEGER")], 25),
    ("sys_role", "系统角色", [("role_id", "INTEGER"), ("role_name", "VARCHAR"), ("remark", "VARCHAR")], 10),
    ("sys_operation_log", "系统操作日志", [("log_id", "INTEGER"), ("user_id", "INTEGER"), ("action", "VARCHAR"), ("created_at", "DATE")], 120),
]


def _decoy_value(rng: random.Random, column: str, data_type: str) -> object:
    if data_type == "INTEGER":
        return rng.randint(1, 999)
    if data_type == "DECIMAL":
        return q(Decimal(str(round(rng.uniform(1, 9999), 2))))
    if data_type == "DATE":
        return date(2023, 1, 1) + timedelta(days=rng.randint(0, 1000))
    if data_type == "BOOLEAN":
        return rng.random() < 0.5
    if column.endswith("status"):
        return rng.choice(STATUS_WORDS)
    if column.endswith("_type"):
        return rng.choice(TYPE_WORDS)
    return f"{column}_{rng.randint(1, 40)}"


def build_decoys(conn: duckdb.DuckDBPyConnection) -> int:
    """造 60 张与本项目无关但结构合法的表，用来模拟真实数仓的 schema 规模。"""
    rng = random.Random(DECOY_SEED)
    for name, _comment, columns, n_rows in DECOY_TABLES:
        ddl = ", ".join(f'"{col}" {typ}' for col, typ in columns)
        conn.execute(f'CREATE TABLE "{name}" ({ddl})')
        rows = [
            tuple(_decoy_value(rng, col, typ) for col, typ in columns)
            for _ in range(n_rows)
        ]
        placeholders = ", ".join("?" for _ in columns)
        conn.executemany(f'INSERT INTO "{name}" VALUES ({placeholders})', rows)
    return len(DECOY_TABLES)


def _imported_table_count() -> int | None:
    """看看现有库里有没有用户导入的表；读不到（比如服务正占着）返回 None。"""
    if not DB_PATH.exists():
        return 0
    try:
        con = duckdb.connect(str(DB_PATH), read_only=True)
        count = con.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name LIKE 'imp_%'"
        ).fetchone()[0]
        con.close()
        return int(count)
    except Exception:
        return None


def main() -> None:
    imported = _imported_table_count()
    force = "--force" in sys.argv
    if imported is None:
        print("读不到现有数据库：web 服务可能正在占用它（先停服务，或加 --force 强制重建）")
        if not force:
            return
    elif imported > 0 and not force:
        print(f"现有库里有 {imported} 张导入的表（imp_*）。重建会把它们一起删掉。")
        print("确认要清空请加 --force：python scripts/build_db.py --force")
        return

    rng = random.Random(SEED)
    users = build_users(rng)
    products = build_products(rng)
    orders, items, payments, refunds = build_orders(rng, products)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = duckdb.connect(str(DB_PATH))
    # 关掉自动 checkpoint、把所有写入放进一个事务。
    # 不这么做的话每条语句都要 fsync 一次 WAL：在本地 SSD 上没感觉，
    # 但在 Docker 卷 / 网络盘上会慢到 40KB/s（实测 2 分钟还没建完）。
    conn.execute("PRAGMA checkpoint_threshold='2GB'")
    conn.execute("BEGIN TRANSACTION")
    for stmt in SCHEMA_PATH.read_text(encoding="utf-8").split(";"):
        if stmt.strip():
            conn.execute(stmt)

    insert(conn, "INSERT INTO channels VALUES (?, ?, ?)", CHANNELS)
    insert(conn, "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?)", users)
    insert(conn, "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?)", products)
    insert(conn, "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", orders)
    insert(conn, "INSERT INTO order_items VALUES (?, ?, ?, ?, ?, ?)", items)
    insert(conn, "INSERT INTO payments VALUES (?, ?, ?, ?, ?, ?)", payments)
    insert(conn, "INSERT INTO refunds VALUES (?, ?, ?, ?, ?, ?)", refunds)
    n_decoys = build_decoys(conn)
    conn.commit()

    print(f"built {DB_PATH}")
    for table in ("channels", "users", "products", "orders", "order_items", "payments", "refunds"):
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:<12} {n:>7}")
    print(f"  + {n_decoys} 张无关表（模拟真实数仓的 schema 规模）")

    if "--verify" in sys.argv:
        print("\nkey figures (评测集 gold SQL 的口径基准):")
        queries = {
            "2025 GMV": """
                SELECT ROUND(SUM(pay_amount), 2) FROM orders
                WHERE status IN ('已支付','已发货','已完成')
                  AND order_time >= TIMESTAMP '2025-01-01' AND order_time < TIMESTAMP '2026-01-01'
            """,
            "2024 GMV": """
                SELECT ROUND(SUM(pay_amount), 2) FROM orders
                WHERE status IN ('已支付','已发货','已完成')
                  AND order_time >= TIMESTAMP '2024-01-01' AND order_time < TIMESTAMP '2025-01-01'
            """,
            "2025 有效订单数": """
                SELECT COUNT(*) FROM orders
                WHERE status IN ('已支付','已发货','已完成')
                  AND order_time >= TIMESTAMP '2025-01-01' AND order_time < TIMESTAMP '2026-01-01'
            """,
            "2025 已完成退款额": """
                SELECT ROUND(SUM(refund_amount), 2) FROM refunds
                WHERE status = '已完成'
                  AND refund_time >= TIMESTAMP '2025-01-01' AND refund_time < TIMESTAMP '2026-01-01'
            """,
        }
        for label, sql in queries.items():
            print(f"  {label:<16} {conn.execute(sql).fetchone()[0]}")
    conn.close()


if __name__ == "__main__":
    main()
