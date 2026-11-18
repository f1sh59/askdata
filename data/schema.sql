-- ============================================================
-- askdata 数据集：模拟一家电商公司的交易数据，用于 Text2SQL 评测。
--
-- 业务口径（评测集里的 gold SQL 全部遵循这三条）：
--   有效订单 = status IN ('已支付', '已发货', '已完成')
--              不含「已取消」「待支付」「已退款」
--   销售额 / GMV = 有效订单的 pay_amount 之和
--              （不是 total_amount：后者是折扣前金额，也不排除取消单）
--   退款金额 = refunds.refund_amount，且仅统计 status = '已完成' 的退款
--              （处理中、已拒绝都不算）
--
-- 刻意保留的脏数据（见 README「口径陷阱」）：
--   users.city / member_level 存在 NULL
--   users.is_deleted 为真时，其历史订单依然有效
--   2% 的订单 total_amount 与明细汇总对不上
--   orders.order_time 是 TIMESTAMP，users.register_date 是 DATE
-- ============================================================

DROP TABLE IF EXISTS refunds;
DROP TABLE IF EXISTS payments;
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS channels;

CREATE TABLE channels (
    channel_id   INTEGER PRIMARY KEY,
    channel_name VARCHAR NOT NULL,
    channel_type VARCHAR NOT NULL
);

CREATE TABLE users (
    user_id       INTEGER PRIMARY KEY,
    user_name     VARCHAR NOT NULL,
    city          VARCHAR,
    province      VARCHAR,
    register_date DATE    NOT NULL,
    member_level  VARCHAR,
    is_deleted    BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE products (
    product_id   INTEGER PRIMARY KEY,
    product_name VARCHAR NOT NULL,
    category     VARCHAR NOT NULL,
    sub_category VARCHAR,
    list_price   DECIMAL(10, 2) NOT NULL,
    cost         DECIMAL(10, 2) NOT NULL,
    launch_date  DATE    NOT NULL,
    is_online    BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE orders (
    order_id        INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL,
    channel_id      INTEGER NOT NULL,
    order_time      TIMESTAMP NOT NULL,
    status          VARCHAR NOT NULL,
    total_amount    DECIMAL(12, 2) NOT NULL,
    discount_amount DECIMAL(12, 2) NOT NULL,
    pay_amount      DECIMAL(12, 2) NOT NULL,
    coupon_code     VARCHAR
);

CREATE TABLE order_items (
    item_id     INTEGER PRIMARY KEY,
    order_id    INTEGER NOT NULL,
    product_id  INTEGER NOT NULL,
    quantity    INTEGER NOT NULL,
    unit_price  DECIMAL(10, 2) NOT NULL,
    item_amount DECIMAL(12, 2) NOT NULL
);

CREATE TABLE payments (
    payment_id INTEGER PRIMARY KEY,
    order_id   INTEGER NOT NULL,
    pay_time   TIMESTAMP NOT NULL,
    pay_method VARCHAR NOT NULL,
    pay_amount DECIMAL(12, 2) NOT NULL,
    pay_status VARCHAR NOT NULL
);

CREATE TABLE refunds (
    refund_id     INTEGER PRIMARY KEY,
    order_id      INTEGER NOT NULL,
    refund_time   TIMESTAMP NOT NULL,
    refund_amount DECIMAL(12, 2) NOT NULL,
    reason        VARCHAR NOT NULL,
    status        VARCHAR NOT NULL
);
