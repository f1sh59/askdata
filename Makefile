# 本机开发 / CI / Docker 三套用法

.PHONY: help install db test validate attack serve accept ci docker-build docker-up clean

PY := python

help:
	@echo "make install       安装依赖"
	@echo "make db            生成数据库（固定种子，可重复）"
	@echo "make test          单元测试（96 个）"
	@echo "make validate      校验评测集（50 题 gold SQL 全部可执行）"
	@echo "make attack        安全闸回归（50 拦 + 10 放）"
	@echo "make serve         本地起服务（http://127.0.0.1:8777）"
	@echo "make accept        接口验收（跳过调模型的部分）"
	@echo "make ci            本地跑一遍 CI 的全部检查"
	@echo "make docker-up     docker compose 一键启动"

install:
	$(PY) -m pip install -r requirements.txt

db:
	$(PY) scripts/build_db.py

test:
	$(PY) -m unittest discover -s tests -t . -v

validate:
	$(PY) -m askdata.cli validate

attack:
	$(PY) -m askdata.cli attack

serve:
	$(PY) -m askdata.server --port 8777

accept:
	$(PY) scripts/acceptance.py --skip-llm

# 和 .github/workflows/ci.yml 等价的本地版本：提交前跑一遍，别等 CI 告诉你
ci: db test validate attack
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

docker-build:
	docker build -t askdata:latest .

docker-up:
	docker compose up -d --build
	@echo "启动后访问 http://127.0.0.1:8777"

clean:
	rm -rf tmp_acceptance .cache
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
