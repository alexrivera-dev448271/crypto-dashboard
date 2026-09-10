PY ?= .venv/bin/python
UV ?= uv
ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

.PHONY: help venv sync audit lint test smoke run build-frontend dev clean git-init codegraph

help:            ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

venv:            ## Create python venv
	$(UV) venv .venv --python 3.11

sync:            ## Install/refresh backend deps (locked)
	$(UV) lock
	$(UV) sync --all-groups

audit:           ## Supply-chain scan: python deps + frontend package-lock
	python -m pip_audit || $(PY) -m pip_audit
	cd frontend && npm audit --omit=dev || true

lint:            ## Ruff lint backend
	.venv/bin/ruff check backend tests

test:            ## Run backend test suite
	$(PY) -m pytest -q

smoke:           ## End-to-end smoke test against a running server (or start one)
	$(PY) scripts/smoke_test.py

run:             ## Run the full stack (embedded postgres + API + built frontend) on :8400
	$(PY) backend/run.py

build-frontend:  ## Build frontend → frontend/dist (served by FastAPI in production mode)
	cd frontend && npm ci && npm run build

dev:             ## Backend only, with live reload of python
	$(PY) backend/run.py --reload

clean:           ## Remove caches and built artifacts
	rm -rf .venv frontend/node_modules frontend/dist data/pytest-tmp
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

codegraph:       ## (Re)index this project in CodeGraph
	~/.local/bin/codegraph sync "$(ROOT)"
