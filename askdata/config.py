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
