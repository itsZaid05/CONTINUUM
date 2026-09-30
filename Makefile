.PHONY: sync test baseline eval eval-b ablate demo kit lint replay help

PY=python3
UV=uv

help:
	@echo "CONTINUUM — Make targets"
	@echo "  make sync        — install deps (uv sync --extra dev, fallback pip)"
	@echo "  make test        — run all tests (offline-fake, deterministic)"
	@echo "  make baseline    — reproduce baseline vs CONTINUUM comparison"
	@echo "  make eval        — full evaluation (arbiter accuracy, shadow, comparison)"
	@echo "  make eval-b      — planner + text runtime + multimodal + ablations"
	@echo "  make kit         — organizer JSONL stdio bridge"
	@echo "  make replay      — demo Delhi→Bangalore replay"
	@echo "  make demo        — serve FastAPI preview (optional)"
	@echo "  make lint        — ruff + mypy"

sync:
	@if command -v uv >/dev/null 2>&1; then \
		uv sync --extra dev; \
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
	ruff check src tests scripts backend examples
	mypy src
	mypy backend

ablate:
	$(PY) -m continuum.cli ablate || echo "ablate not yet"
