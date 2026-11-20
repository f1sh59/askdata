# 多阶段构建：builder 里装依赖，runtime 只搬 venv。
#
# 好处不只是体积——更重要的是**最终镜像里没有 pip、没有编译工具**：
# 攻击面小了，也不会有人在容器里 pip install 改掉运行环境。
#
# 两个踩过的坑：
# 1. `chown -R app:app /opt/venv` 会让 89MB 的 venv 在新层里**完整复制一遍**
#    （镜像从 434MB 涨到 96MB 的额外层）。正确做法是 COPY --chown，元数据随拷贝落盘。
# 2. builder 里创建的 venv 自带 pip/setuptools（约 13MB），装完依赖要卸掉。

# ---------- 构建阶段 ----------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 国内网络直连 pypi 可能被掐，留一个可覆盖的源地址
ARG PIP_INDEX_URL=https://pypi.org/simple

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install -i ${PIP_INDEX_URL} -r requirements.txt \
    # 装完把 pip/setuptools 卸掉：运行时用不到，留着只是多 13MB 和一条改环境的路径
    && /opt/venv/bin/python -m pip uninstall -y pip setuptools \
    && rm -rf /opt/venv/lib/python3.12/site-packages/pip* \
              /opt/venv/lib/python3.12/site-packages/setuptools* \
              /opt/venv/bin/pip* \
    && find /opt/venv -name '__pycache__' -type d -prune -exec rm -rf {} +

# ---------- 运行阶段 ----------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    TZ=Asia/Shanghai \
    ASKDATA_DB=/app/data/askdata.duckdb

# 非 root 运行：容器被攻破时拿不到 root，也改不了 /app 里的代码。
# 必须在 COPY 之前建好用户，才能用 --chown 一步到位。
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app \
    # 基础镜像自带 pip；运行时用不到，删掉才真的"镜像里没有 pip"
    && rm -rf /usr/local/lib/python3.12/site-packages/pip* \
              /usr/local/bin/pip*

WORKDIR /app

# 只有 venv 从构建阶段过来；--chown 避免再起一层 89MB 的复制
COPY --from=builder --chown=app:app /opt/venv /opt/venv

COPY --chown=app:app askdata/ ./askdata/
COPY --chown=app:app web/ ./web/
COPY --chown=app:app scripts/ ./scripts/
COPY --chown=app:app eval/ ./eval/
COPY --chown=app:app reports/ ./reports/
COPY --chown=app:app data/schema.sql ./data/schema.sql
COPY --chown=app:app docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY --chown=app:app README.md ./

RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /app/data \
    && chown app:app /app/data

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
