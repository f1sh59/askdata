# askdata 评测报告

- **策略**：`llm-selfcorrect`
- **数据集**：v1.1（4 题）
- **运行时间**：2026-11-18 18:36:17
- **总耗时**：7.81s

## 1. 总览

| 指标 | 值 |
|------|----|
| Execution Accuracy（严格） | **75.0%**（3/4） |
| Execution Accuracy（忽略列序） | 75.0%（3/4） |
| SQL 平均执行耗时 | 5.5 ms |

### 模型调用

| 项 | 值 |
|----|----|
| 模型 | deepseek-chat |
| 实际调用次数 | 6 |
| 缓存命中 | 0 |
| prompt tokens | 13077 |
| completion tokens | 1013 |
| SQL 解析失败 | 0 |
| 接口报错 | 0 |

### 执行反馈自纠（P3）

| 指标 | 值 |
|------|----|
| 一次通过 EX | 25.0% |
| 自纠后 EX | **75.0%** |
| 修复题数（先错后对） | 2 |
| 改坏题数（先对后错） | 0 |
| 触发重试的题数 | 2 |
| 平均尝试次数 | 1.50 |

- 修复：m10、h08

## 2. 按难度

| 难度 | 题数 | 正确 | EX |
|------|------|------|----|
| medium | 1 | 1 | 100.0% |
| hard | 3 | 2 | 66.7% |

## 3. 失败分布

| 状态 | 题数 | 占比 |
|------|------|------|
| 正确 | 3 | 75.0% |
| 结果值不一致 | 1 | 25.0% |

## 4. 按标签（最容易出错的能力点排在前面）

| 标签 | 题数 | 正确 | EX |
|------|------|------|----|
| window | 1 | 0 | 0.0% |
| subquery | 1 | 0 | 0.0% |
| business_rule | 3 | 2 | 66.7% |
| group | 2 | 2 | 100.0% |
| left_join | 2 | 2 | 100.0% |
| time_bucket | 2 | 2 | 100.0% |
| ratio | 1 | 1 | 100.0% |
| anti_pattern | 1 | 1 | 100.0% |
| having | 1 | 1 | 100.0% |

## 5. 逐题结果

| ID | 难度 | 状态 | 尝试 | gold 行数 | pred 行数 | 题目 |
|----|------|------|------|-----------|-----------|------|
| m10 | medium | 正确 | 2 | 12 | 12 | 2025 年每个月的退款率是多少？（退款率 = 已完成退款金额 / 销售额 ×… |
| h05 | hard | 结果值不一致 | 1 | 1 | 1 | 2025 年的订单中，用户相邻两次有效下单之间间隔天数的中位数是多少？ |
| h07 | hard | 正确 | 1 | 1 | 1 | 2025 年下过单、但在 2025 年的订单里一次都没有支付成功的用户有多少个？ |
| h08 | hard | 正确 | 2 | 12 | 12 | 2025 年每个月的复购用户数是多少？（当月有效订单不少于 2 笔的用户） |

## 6. 失败明细

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

