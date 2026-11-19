# 多阶段构建：builder 里装依赖，runtime 只搬 venv。
#
# 好处不只是体积——更重要的是**最终镜像里没有 pip、没有编译工具**：
# 攻击面小了，也不会有人在容器里 pip install 改掉运行环境。

# ---------- 构建阶段 ----------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 国内网络直连 pypi 会很慢甚至超时，留一个可覆盖的源地址
ARG PIP_INDEX_URL=https://pypi.org/simple

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install -i ${PIP_INDEX_URL} -r requirements.txt

# ---------- 运行阶段 ----------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    TZ=Asia/Shanghai \
    ASKDATA_DB=/app/data/askdata.duckdb

# 非 root 运行：容器被攻破时拿不到 root，也改不了 /app 里的代码
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app

WORKDIR /app

# 只有 venv 从构建阶段过来，pip 和编译产物都不进最终镜像
COPY --from=builder /opt/venv /opt/venv

COPY askdata/ ./askdata/
COPY web/ ./web/
COPY scripts/ ./scripts/
COPY eval/ ./eval/
COPY reports/ ./reports/
COPY data/schema.sql ./data/schema.sql
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY README.md ./

RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /app/data \
    && chown -R app:app /app /opt/venv

# 数据库挂在卷里，重建容器不丢数据；用非 root 用户启动
VOLUME ["/app/data"]
USER app

EXPOSE 8777

# 就绪探针（不是存活）：真的查一次库，避免"容器在、查询全挂"还接流量
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8777/ready',timeout=4).status==200 else 1)"

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# 容器里必须监听 0.0.0.0，否则宿主机访问不到
CMD ["python", "-m", "askdata.server", "--host", "0.0.0.0", "--port", "8777"]
