"""打包给别人看：只带源码、文档、截图和一个现成的演示库。

刻意不做的事：
    * 不带 .env（里面有 API key）——扫描一遍确认没有
    * 不带 .venv / .git / node_modules / 缓存（别人不需要，也不该看历史）
    * 不带评测脚本的临时产物（tmp*、*.log、.coverage）

为什么带 data/askdata.duckdb：
    对方如果只想看看，装完依赖就能直接提问；不然要先跑 36 秒建库。
    19MB 压缩后大概 5~6MB，可以接受。
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "askdata-1.1.0"
STAGE = ROOT / "dist" / NAME
ZIP = ROOT / "dist" / f"{NAME}.zip"

INCLUDE_DIRS = ["askdata", "web", "tests", "scripts", "eval", "reports", "docs"]
INCLUDE_FILES = [
    "README.md",
    "QUICKSTART.md",
    "CHANGELOG.md",
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    "docker-entrypoint.sh",
    "pyproject.toml",
    "requirements.txt",
    ".env.example",
    ".dockerignore",
    ".gitignore",
    ".gitattributes",
]

SKIP_PATTERNS = ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "*.pyc", ".DS_Store")


def want(path: Path) -> bool:
    if any(part in SKIP_PATTERNS or part.startswith("tmp") for part in path.parts):
        return False
    return not path.name.endswith(".log")


def main() -> int:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)

    copied = 0
    for name in INCLUDE_FILES:
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, STAGE / name)
            copied += 1

    for name in INCLUDE_DIRS:
        src_dir = ROOT / name
        if not src_dir.exists():
            continue
        for src in src_dir.rglob("*"):
            if not src.is_file() or not want(src.relative_to(ROOT)):
                continue
            if src.suffix in (".db", ".jsonl") and src.parent.name == "data":
                continue  # 会话库/审计日志是运行产物
            dst = STAGE / src.relative_to(ROOT)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1

    # 演示库单独放：对方装完依赖就能直接问
    db = ROOT / "data" / "askdata.duckdb"
    if db.exists():
        (STAGE / "data").mkdir(exist_ok=True)
        shutil.copy2(db, STAGE / "data" / "askdata.duckdb")
        shutil.copy2(ROOT / "data" / "schema.sql", STAGE / "data" / "schema.sql")
        copied += 2

    print(f"  已复制 {copied} 个文件")

    # 密钥扫描：宁可多扫一遍，也不能把 key 打包出去
    secret = ""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("DEEPSEEK_API_KEY="):
                secret = line.split("=", 1)[1].strip()
    # 只认"像真 key"的串：sk- 后面至少 24 位。
    # 第一次写成了裸 "sk-" 判断，结果把测试里的 sk-test / sk-abc 全报成密钥——
    # 扫描器误报太多，人就会开始忽略它的输出，那它就失去意义了。
    import re

    key_pattern = re.compile(r"sk-[A-Za-z0-9_-]{24,}")
    hits = []
    for path in STAGE.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if secret and secret in text:
            hits.append(str(path.relative_to(STAGE)))
        elif key_pattern.search(text):
            hits.append(str(path.relative_to(STAGE)))
    if hits:
        print("  !! 发现疑似密钥，已中止：", hits[:5])
        return 1
    print("  密钥扫描：通过（没有 .env、没有明文 key）")

    if ZIP.exists():
        ZIP.unlink()
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in STAGE.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(STAGE.parent))
    size = ZIP.stat().st_size / (1024 * 1024)
    print(f"  压缩包：{ZIP}  {size:.1f} MB")
    shutil.rmtree(STAGE)
    print(f"  解压后目录大小：{sum(p.stat().st_size for p in ROOT.joinpath('docs').rglob('*') if p.is_file()) / 1048576:.1f} MB（文档截图为主）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
