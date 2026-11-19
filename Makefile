# 本机开发 / CI / Docker 三套用法

.PHONY: help install install-dev db test test-cov lint typecheck check validate attack serve accept ci load docker-build docker-up clean

PY := python

help:
	@echo "make install       安装运行时依赖"
	@echo "make install-dev   安装开发工具（ruff / mypy / pytest / pytest-cov）"
	@echo "make db            生成数据库（固定种子，可重复）"
	@echo "make test          pytest 全量测试"
	@echo "make test-cov      测试 + 覆盖率（有门槛）"
	@echo "make lint          ruff 静态检查"
	@echo "make typecheck     mypy 类型检查"
	@echo "make check         lint + typecheck + test（提交前跑这个）"
	@echo "make validate      校验评测集（50 题 gold SQL 全部可执行）"
	@echo "make attack        安全闸回归（50 拦 + 10 放）"
	@echo "make serve         本地起服务（http://127.0.0.1:8777）"
	@echo "make accept        接口验收（跳过调模型的部分）"
	@echo "make ci            本地跑一遍 CI 的全部检查"
	@echo "make load          压测并生成 reports/perf_*.md（需先关限流）"
	@echo "make docker-up     docker compose 一键启动"

install:
	$(PY) -m pip install -r requirements.txt

# 开发工具链：lint + 类型 + 测试 + 覆盖率
install-dev:
	$(PY) -m pip install ruff mypy pytest pytest-cov

db:
	$(PY) scripts/build_db.py

test:
	$(PY) -m pytest -q

test-cov:
	$(PY) -m pytest --cov --cov-report=term-missing -q

lint:
	$(PY) -m ruff check askdata tests scripts

typecheck:
	$(PY) -m mypy

# 提交前的门槛：任何一条挂了都别提交
check: lint typecheck test-cov

validate:
	$(PY) -m askdata.cli validate

attack:
	$(PY) -m askdata.cli attack

serve:
	$(PY) -m askdata.server --port 8777

accept:
	$(PY) scripts/acceptance.py --skip-llm

# 和 .github/workflows/ci.yml 等价的本地版本：提交前跑一遍，别等 CI 告诉你
ci: lint typecheck test-cov db validate attack
	@echo "==> 起服务跑接口验收"
	@$(PY) -m askdata.server --port 8777 > /tmp/askdata-ci.log 2>&1 & \
	pid=$$!; \
	for i in $$(seq 1 60); do \
	  curl -sf http://127.0.0.1:8777/api/overview > /dev/null && break; sleep 1; \
	done; \
	$(PY) scripts/acceptance.py --skip-llm; \
	$(PY) scripts/fetch_snapshots.py http://127.0.0.1:8777; \
	node scripts/frontend_audit.js; \
	kill $$pid || true

load:
	@echo "压测需要关闭限流：ASKDATA_RATE_LIMIT=0 make serve"
	$(PY) scripts/load_test.py --levels 1,4,8,16,32 --duration 6

docker-build:
	docker build -t askdata:latest .

docker-up:
	docker compose up -d --build
	@echo "启动后访问 http://127.0.0.1:8777"

clean:
	rm -rf tmp_acceptance .cache .coverage htmlcov
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
