# askdata 评测报告

- **策略**：`llm-selfcorrect`
- **数据集**：v1.1（30 题）
- **运行时间**：2026-11-18 18:37:58
- **总耗时**：35.63s

## 1. 总览

| 指标 | 值 |
|------|----|
| Execution Accuracy（严格） | **90.0%**（27/30） |
| Execution Accuracy（忽略列序） | 90.0%（27/30） |
| SQL 平均执行耗时 | 3.8 ms |

### 模型调用

| 项 | 值 |
|----|----|
| 模型 | deepseek-chat |
| 实际调用次数 | 32 |
| 缓存命中 | 0 |
| prompt tokens | 67228 |
| completion tokens | 3856 |
| SQL 解析失败 | 0 |
| 接口报错 | 0 |

### 执行反馈自纠（P3）

| 指标 | 值 |
|------|----|
| 一次通过 EX | 83.3% |
| 自纠后 EX | **90.0%** |
| 修复题数（先错后对） | 2 |
| 改坏题数（先对后错） | 0 |
| 触发重试的题数 | 2 |
| 平均尝试次数 | 1.07 |

- 修复：m10、h08

## 2. 按难度

| 难度 | 题数 | 正确 | EX |
|------|------|------|----|
| easy | 10 | 9 | 90.0% |
| medium | 12 | 12 | 100.0% |
| hard | 8 | 6 | 75.0% |

## 3. 失败分布

| 状态 | 题数 | 占比 |
|------|------|------|
| 正确 | 27 | 90.0% |
| 结果值不一致 | 2 | 6.7% |
| 列数不一致 | 1 | 3.3% |

## 4. 按标签（最容易出错的能力点排在前面）

| 标签 | 题数 | 正确 | EX |
|------|------|------|----|
| window | 4 | 2 | 50.0% |
| subquery | 2 | 1 | 50.0% |
| top_n | 5 | 4 | 80.0% |
| join | 7 | 6 | 85.7% |
| time_bucket | 7 | 6 | 85.7% |
| business_rule | 16 | 14 | 87.5% |
| group | 15 | 14 | 93.3% |
| ratio | 7 | 7 | 100.0% |
| count | 5 | 5 | 100.0% |
| time_filter | 5 | 5 | 100.0% |
| null_handling | 4 | 4 | 100.0% |
| aggregate | 3 | 3 | 100.0% |
| having | 3 | 3 | 100.0% |
| payment | 2 | 2 | 100.0% |
| distinct | 2 | 2 | 100.0% |
| yoy | 2 | 2 | 100.0% |
| left_join | 2 | 2 | 100.0% |
| filter | 1 | 1 | 100.0% |
| date_type | 1 | 1 | 100.0% |
| conditional_agg | 1 | 1 | 100.0% |
| anti_pattern | 1 | 1 | 100.0% |

## 5. 逐题结果

| ID | 难度 | 状态 | 尝试 | gold 行数 | pred 行数 | 题目 |
|----|------|------|------|-----------|-----------|------|
| e01 | easy | 正确 | 1 | 1 | 1 | 2025 年一共有多少笔订单？ |
| e02 | easy | 正确 | 1 | 1 | 1 | 目前在售（上架）的商品有多少个？ |
| e03 | easy | 正确 | 1 | 1 | 1 | 2025 年的销售额（GMV）是多少？ |
| e04 | easy | 正确 | 1 | 1 | 1 | 2025 年已完成状态的退款金额合计是多少？ |
| e05 | easy | 正确 | 1 | 1 | 1 | 平台目前有多少个未注销的注册用户？ |
| e06 | easy | 正确 | 1 | 4 | 4 | 各支付方式的支付成功金额分别是多少？ |
| e07 | easy | 正确 | 1 | 1 | 1 | 2025 年有效订单的平均订单金额是多少？ |
| e08 | easy | 正确 | 1 | 1 | 1 | 商品一共有几个品类？ |
| e09 | easy | 正确 | 1 | 1 | 1 | 2025 年有多少个不同用户下过有效订单？ |
| e10 | easy | 结果值不一致 | 1 | 1 | 1 | 2025 年销售额最高的商品品类是哪个？它的销售额是多少？（返回两列：品类、销… |
| m01 | medium | 正确 | 1 | 12 | 12 | 2025 年每个月的销售额分别是多少？ |
| m02 | medium | 正确 | 1 | 11 | 11 | 2025 年每个月新增的注册用户数是多少？ |
| m03 | medium | 正确 | 1 | 1 | 1 | 2025 年销售额相比 2024 年同比增长了百分之多少？ |
| m04 | medium | 正确 | 1 | 5 | 5 | 2025 年销售额排名前 5 的商品是哪些？请给出商品名称和销售额。 |
| m05 | medium | 正确 | 1 | 5 | 5 | 2025 年各渠道的有效订单数和销售额分别是多少？ |
| m06 | medium | 正确 | 1 | 10 | 10 | 用户数最多的前 10 个省份是哪些？分别有多少用户？ |
| m07 | medium | 正确 | 1 | 5 | 5 | 2025 年各品类的销售额和毛利率分别是多少？（毛利率 =（销售额 - 成本）… |
| m08 | medium | 正确 | 1 | 1 | 1 | 2025 年下过 2 笔及以上有效订单的用户有多少个？ |
| m09 | medium | 正确 | 1 | 4 | 4 | 2025 年各支付方式的订单数占比是多少？（按支付成功流水统计） |
| m10 | medium | 正确 | 2 | 12 | 12 | 2025 年每个月的退款率是多少？（退款率 = 已完成退款金额 / 销售额 ×… |
| m11 | medium | 正确 | 1 | 5 | 5 | 2025 年客单价最高的前 5 个城市是哪些？请给出城市、订单数和客单价。（只… |
| m12 | medium | 正确 | 1 | 9 | 9 | 2025 年每个月的新客首单数是多少？（新客 = 该用户第一次下有效订单） |
| h01 | hard | 正确 | 1 | 12 | 12 | 2025 年每月销售额的环比增长率（百分比数值，保留 2 位小数）是多少？ |
| h02 | hard | 正确 | 1 | 5 | 5 | 2025 年每个品类中销售额最高的商品分别是哪个？请给出品类、商品名称和销售额。 |
| h03 | hard | 列数不一致 | 1 | 12 | 12 | 2025 年每个月的累计销售额是多少？ |
| h04 | hard | 正确 | 1 | 5 | 5 | 2025 年各渠道的销售额相比 2024 年同比增长了多少？请给出渠道、202… |
| h05 | hard | 结果值不一致 | 1 | 1 | 1 | 2025 年的订单中，用户相邻两次有效下单之间间隔天数的中位数是多少？ |
| h06 | hard | 正确 | 1 | 5 | 5 | 2025 年各会员等级的订单数、销售额和销售额占比分别是多少？（占比为百分比数… |
| h07 | hard | 正确 | 1 | 1 | 1 | 2025 年下过单、但在 2025 年的订单里一次都没有支付成功的用户有多少个？ |
| h08 | hard | 正确 | 2 | 12 | 12 | 2025 年每个月的复购用户数是多少？（当月有效订单不少于 2 笔的用户） |

## 6. 失败明细

### e10 · easy · 结果值不一致

- 问题：2025 年销售额最高的商品品类是哪个？它的销售额是多少？（返回两列：品类、销售额）
- 口径：品类销售额按明细 item_amount 汇总；题目已明确要求两列

```sql
-- gold
SELECT p.category, ROUND(SUM(oi.item_amount), 2) AS amt FROM order_items oi JOIN orders o ON o.order_id = oi.order_id JOIN products p ON p.product_id = oi.product_id WHERE o.status IN ('已支付', '已发货', '已完成') AND o.order_time >= TIMESTAMP '2025-01-01' AND o.order_time < TIMESTAMP '2026-01-01' GROUP BY p.category ORDER BY amt DESC LIMIT 1

-- generated
SELECT p.category, SUM(o.pay_amount) AS sales_amount
FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
JOIN products p ON oi.product_id = p.product_id
WHERE o.status IN ('已支付', '已发货', '已完成')
  AND o.order_time >= TIMESTAMP '2025-01-01'
  AND o.order_time < TIMESTAMP '2026-01-01'
GROUP BY p.category
ORDER BY sales_amount DESC
LIMIT 1
```

### h03 · hard · 列数不一致

- 问题：2025 年每个月的累计销售额是多少？
- 口径：累计值 = 年初到当月的销售额

```sql
-- gold
WITH m AS (SELECT strftime(order_time, '%Y-%m') AS ym, SUM(pay_amount) AS amt FROM orders WHERE status IN ('已支付', '已发货', '已完成') AND order_time >= TIMESTAMP '2025-01-01' AND order_time < TIMESTAMP '2026-01-01' GROUP BY ym) SELECT ym, ROUND(SUM(amt) OVER (ORDER BY ym), 2) AS cum_amt FROM m ORDER BY ym

-- generated
SELECT
    strftime(o.order_time, '%Y-%m') AS month,
    SUM(o.pay_amount) AS monthly_sales,
    SUM(SUM(o.pay_amount)) OVER (ORDER BY strftime(o.order_time, '%Y-%m')) AS cumulative_sales
FROM orders o
WHERE o.status IN ('已支付', '已发货', '已完成')
  AND o.order_time >= TIMESTAMP '2025-01-01'
  AND o.order_time < TIMESTAMP '2026-01-01'
GROUP BY strftime(o.order_time, '%Y-%m')
ORDER BY month
```

### h05 · hard · 结果值不一致

- 问题：2025 年的订单中，用户相邻两次有效下单之间间隔天数的中位数是多少？
- 口径：同用户同日多单的间隔为 0；中位数按天算

```sql
-- gold
WITH o AS (SELECT user_id, order_time, LAG(order_time) OVER (PARTITION BY user_id ORDER BY order_time) AS prev_time FROM orders WHERE status IN ('已支付', '已发货', '已完成')) SELECT ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY date_diff('day', prev_time, order_time)), 1) AS median_gap_days FROM o WHERE prev_time IS NOT NULL AND order_time >= TIMESTAMP '2025-01-01' AND order_time < TIMESTAMP '2026-01-01'

-- generated
WITH valid_orders AS (
    SELECT
        user_id,
        order_time,
        LAG(order_time) OVER (PARTITION BY user_id ORDER BY order_time) AS prev_order_time
    FROM orders
    WHERE status IN ('已支付', '已发货', '已完成')
      AND order_time >= TIMESTAMP '2025-01-01'
      AND order_time < TIMESTAMP '2026-01-01'
),
intervals AS (
    SELECT
        user_id,
        date_diff('day', prev_order_time, order_time) AS gap_days
    FROM valid_orders
    WHERE prev_order_time IS NOT NULL
)
SELECT
    median(gap_days) AS median_gap_days
FROM intervals
```

