.DEFAULT_GOAL := help
COMPOSE := docker compose

.PHONY: help up down logs ps restart shell migrate revision test lint fmt check clean

help: ## 显示可用命令
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "};{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'

up: ## 启动全部服务
	$(COMPOSE) up -d --build
	@echo "API      http://localhost:8000/docs"
	@echo "MinIO    http://localhost:9001"

down: ## 停止服务（保留数据）
	$(COMPOSE) down

clean: ## 停止服务并删除数据卷（会清空数据库）
	$(COMPOSE) down -v

logs: ## 跟踪 api + worker 日志
	$(COMPOSE) logs -f api worker

ps: ## 查看服务状态
	$(COMPOSE) ps

restart: ## 重启 api 与 worker
	$(COMPOSE) restart api worker

shell: ## 进入 api 容器
	$(COMPOSE) exec api bash

migrate: ## 应用数据库迁移
	$(COMPOSE) exec api alembic upgrade head

revision: ## 生成迁移，用法：make revision m="add users"
	$(COMPOSE) exec api alembic revision --autogenerate -m "$(m)"

test: ## 运行测试
	$(COMPOSE) exec api pytest

lint: ## 静态检查
	$(COMPOSE) exec api ruff check .
	$(COMPOSE) exec api mypy apps worker packages

fmt: ## 格式化
	$(COMPOSE) exec api ruff format .
	$(COMPOSE) exec api ruff check --fix .

check: lint test ## 提交前全量检查
