import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

# 允许用 ASKDATA_DB 覆盖数据库路径：容器里数据库挂在卷上，
# 测试时也常需要指向一个临时文件，硬编码路径就没法这么做。
DB_PATH = Path(os.environ.get("ASKDATA_DB") or (DATA_DIR / "askdata.duckdb"))

EVAL_DIR = ROOT / "eval"
DEFAULT_QUESTIONS = EVAL_DIR / "questions.json"
REPORT_DIR = ROOT / "reports"

# 用户表、会话库、审计日志都放在 data/ 下（容器里整个 data 挂卷）
# 都能用环境变量覆盖：测试要指向临时文件，容器里要落到卷上。
USERS_PATH = Path(os.environ.get("ASKDATA_USERS") or (DATA_DIR / "users.json"))
SESSIONS_PATH = Path(os.environ.get("ASKDATA_SESSIONS") or (DATA_DIR / "sessions.db"))
AUDIT_PATH = Path(os.environ.get("ASKDATA_AUDIT") or (DATA_DIR / "audit.jsonl"))

# 缓存放在 data/ 而不是仓库根：容器里 /app 属于 root，非 root 用户写不进去。
CACHE_DIR = Path(os.environ.get("ASKDATA_CACHE") or (DATA_DIR / ".cache"))
