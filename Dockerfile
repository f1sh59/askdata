# 单阶段镜像：应用本身零外部依赖（DuckDB 是嵌入式文件库），不需要多阶段构建
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 先装依赖，利用镜像层缓存：改代码不会重装依赖
COPY requirements.txt .
# 国内网络直连 pypi 会很慢甚至超时，所以留一个可覆盖的源地址：
#   docker build --build-arg PIP_INDEX_URL=https://mirrors.huaweicloud.com/repository/pypi/simple .
# 默认走官方源，在有正常网络的环境（CI、海外服务器）不需要改任何东西。
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} -r requirements.txt

COPY askdata/ ./askdata/
COPY web/ ./web/
COPY scripts/ ./scripts/
COPY eval/ ./eval/
COPY reports/ ./reports/
# data/ 里只有 schema.sql 需要打进来；数据库文件由 entrypoint 首次启动时生成
COPY data/schema.sql ./data/schema.sql
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY README.md ./

RUN chmod +x /usr/local/bin/docker-entrypoint.sh && mkdir -p /app/data

# 导入功能需要写库，数据库放在卷里，避免容器重建丢数据
VOLUME ["/app/data"]

EXPOSE 8777

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8777/api/overview', timeout=4)"

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# 容器里必须监听 0.0.0.0，否则宿主机访问不到
CMD ["python", "-m", "askdata.server", "--host", "0.0.0.0", "--port", "8777"]
