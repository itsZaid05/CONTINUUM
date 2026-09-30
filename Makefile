.PHONY: sync test baseline eval eval-b ablate demo kit lint replay help

# Prefer a globally installed uv. If uv created ``.venv`` but is no longer on
# PATH (common in a fresh shell), use that environment directly rather than
# silently falling back to an unrelated system Python.
UV_BIN := $(firstword $(shell command -v uv 2>/dev/null) $(wildcard .venv/bin/uv))
VENV_PY := $(wildcard .venv/bin/python)
ifneq ($(UV_BIN),)
PY ?= $(UV_BIN) run --frozen python
RUN := $(UV_BIN) run --frozen
else ifneq ($(VENV_PY),)
PY ?= $(VENV_PY)
RUN := $(VENV_PY) -m
else
PY ?= python3
RUN :=
endif

help:
	@echo "CONTINUUM — Make targets"
	@echo "  make sync        — install locked dev deps (uv sync --frozen --extra dev, fallback pip)"
	@echo "  make test        — run all tests (offline-fake, deterministic)"
	@echo "  make baseline    — reproduce baseline vs CONTINUUM comparison"
	@echo "  make eval        — full evaluation (arbiter accuracy, shadow, comparison)"
	@echo "  make eval-b      — planner + text runtime + multimodal + ablations"
	@echo "  make kit         — organizer JSONL stdio bridge"
	@echo "  make replay      — demo Delhi→Bangalore replay"
	@echo "  make demo        — serve FastAPI preview (optional)"
	@echo "  make lint        — ruff + mypy"

sync:
	@if [ -n "$(UV_BIN)" ]; then \
		$(UV_BIN) sync --frozen --extra dev; \
	else \
		$(PY) -m pip install -e ".[dev]"; \
	fi

test:
	$(PY) -m pytest -q

baseline:
	$(PY) -m continuum.cli compare --output reports/comparison.json || true
	@cat reports/comparison.json 2>/dev/null | head -n 100 || echo "no comparison yet"
	@cat reports/comparison.md 2>/dev/null | head -n 100 || echo "no md yet"

eval: test lint
	@echo "== Arbiter accuracy =="
	$(PY) -m continuum.cli eval-arbiter --gold data/gold/arbiter_100.jsonl --output reports/arbiter_accuracy.json || true
	@cat reports/arbiter_accuracy.json | head -n 80 || true
	@echo "== Comparison =="
	$(PY) -m continuum.cli compare --output reports/comparison.json || true
	@cat reports/comparison.md || true
	@echo "== All reports =="
	@ls -lh reports/

eval-b:
	$(PY) -m continuum.cli eval-planner
	$(PY) -m continuum.cli eval-runtime
	$(PY) -m continuum.cli eval-multimodal
	@cat reports/planner_eval.md reports/runtime_eval.md reports/multimodal_eval.md

kit:
	$(PY) -m continuum.cli kit

replay:
	$(PY) -m continuum.cli replay data/scenarios/delhi_bangalore.json --trace

demo:
	@echo "Serving demo API on 0.0.0.0:8000 (preview: https://8000-*.e2b.app)"
	$(PY) -m uvicorn continuum.api:app --host 0.0.0.0 --port 8000 --reload || $(PY) -m continuum.cli serve --host 0.0.0.0 --port 8000

lint:
	$(RUN) ruff check src tests scripts backend examples
	$(RUN) mypy src
	$(RUN) mypy backend

ablate:
	$(PY) -m continuum.cli ablate || echo "ablate not yet"
