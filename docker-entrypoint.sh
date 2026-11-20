#!/bin/sh
# 容器启动脚本：第一次运行时生成数据库，之后直接复用卷里的数据。
#
# 为什么不在 Dockerfile 里 RUN build_db.py：
#   1. 数据库有 19MB，打进镜像会让每一层都变重；
#   2. 构建时生成会占满构建容器的内存（2GB 的 Docker VM 里会被 OOM 杀掉）；
#   3. 数据库本来就要挂在卷上，第一次启动时生成最自然，重建容器也不会丢数据。

set -e

DB_PATH="${ASKDATA_DB:-/app/data/askdata.duckdb}"

mkdir -p "$(dirname "$DB_PATH")"
# 缓存也放在卷里：/app 属于 root，非 root 用户只能写 data/
mkdir -p "${ASKDATA_CACHE:-/app/data/.cache}"

if [ ! -f "$DB_PATH" ]; then
    echo "[entrypoint] 数据库不存在，开始生成：$DB_PATH"
    python scripts/build_db.py
    echo "[entrypoint] 数据库生成完成"
else
    SIZE=$(du -h "$DB_PATH" 2>/dev/null | cut -f1 || echo "?")
    echo "[entrypoint] 复用已有数据库：$DB_PATH ($SIZE)"
fi

exec "$@"
