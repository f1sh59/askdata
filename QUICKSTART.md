# askdata 快速上手

一个中文问数（Text2SQL）系统：你用中文提问，它把问题翻成 SQL、跑出结果，并把
「为什么这么查」的每一步摊开给你看。

这个包是为**看效果**准备的，含 68 张表的演示库（9,748 条订单）。

---

## 最快的方式：Docker

```bash
docker compose up -d --build
```

打开 <http://127.0.0.1:8777>。

> 如果构建时卡在 pip（国内网络），用镜像源：
> ```bash
> docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple -t askdata:1.1.0 .
> docker compose up -d
> ```

---

## 本地跑（Python 3.12+）

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows；Linux/macOS 用 source .venv/bin/activate
pip install -r requirements.txt
python -m askdata.server --port 8777
```

打开 <http://127.0.0.1:8777>。

---

## 不填 API Key 能看到什么

「问数」页需要模型 Key（点右上角「API」填入，或在 `.env` 里设 `DEEPSEEK_API_KEY`）。
**其余页面全部免 Key 可用**：

| 页面 | 看什么 |
|---|---|
| 数据字典 | 68 张表的字段、口径、示例值 |
| 评测 | 50 题的准确率、失败分布、逐题结果 |
| 安全闸 | 手输 SQL，看四道闸怎么拦注入、怎么放行正常查询 |
| 澄清 | 含糊问题的反问策略与收敛数据 |
| 说明 | 这个项目在解决什么问题、每个数字怎么来的 |
| `/docs` | OpenAPI 3.1 接口文档（23 个接口） |

---

## 几个值得点的验证

```bash
python -m askdata.cli validate     # 50 题标答全部可执行
python -m askdata.cli attack       # 50 条注入全拦、10 条正常零误伤
python -m pytest -q                # 300+ 测试，覆盖率 85%
```

---

## 目录结构

```
askdata/     服务、问数管线、安全闸、检索、会话、用户、审计
web/         零构建前端（原生 JS + CSS，没有 npm）
tests/       单元 + 集成测试
scripts/     验收、压测、截图、打包等工具
eval/        评测题集（每题配可执行标答）
reports/     历史评测报告（JSON + Markdown）
docs/        截图与设计文档
data/        演示数据库 + schema
```

---

## 已知限制（写在明处的）

- 单条 DuckDB 连接 + 全局锁：并发 16 之后吞吐不再增长（有意取舍）
- 鉴权是三档模式（不鉴权 / 共享 token / 多用户），不是完整的 SSO
- 会话默认存 SQLite，重启不丢，但没做多实例共享

更多细节见 `README.md` 和 `CHANGELOG.md`。
