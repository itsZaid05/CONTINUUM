# Submission Audit — 2026-09-30

This is a release-readiness audit of the repository at the submission branch,
not an FDB-v3 score claim. Commands below were run from a clean Python 3.11
virtual environment unless noted otherwise.

## Result

**Core release gate: PASS.** The installation gap that previously made
`pytest` fail during backend-test collection was fixed: FastAPI, Uvicorn, and
python-dotenv are now declared package dependencies rather than only appearing
in a separate `requirements.txt`. The latter is now a compatibility entry point
to the canonical `pyproject.toml` definition.

The FDB LiveKit optional environment also had an upstream transitive dependency
break: the newest OpenTelemetry version removed `LogData`, which
LiveKit Agents 1.3.11 imports. The FDB extra now pins the known-compatible
LiveKit and OpenTelemetry family. Both the lockfile and installation tests
verify that combination. The audited demo backend is now fully linted/type-
checked and no longer derives visible flight/effect identifiers from Python's
process-salted `hash()`.

A follow-up runtime review found and corrected a functional FDB integration
defect: the worker had a named LiveKit dispatch but the pinned, unmodified
upstream inference client only creates/joins a new room and sends no named
dispatch request. The worker and Phase 3 smoke backend now intentionally use
LiveKit automatic dispatch. Regression tests assert the empty dispatch name and
that the smoke room has no duplicate explicit agent dispatch. Use an isolated
LiveKit project for official evaluation because automatic dispatch joins every
new room in that project.

## Gates executed

| Gate | Command | Observed result |
|---|---|---|
| Fresh locked core install | `uv sync --frozen --extra dev && python -m pytest -ra` | **331 passed, 8 skipped** (optional LiveKit and recognizer paths absent) |
| Locked core install | `uv lock --check && uv sync --frozen --extra dev` | passed |
| Full test suite with FDB dependencies | `uv sync --frozen --extra dev --extra fdb && python -m pytest -ra` | **334 passed, 5 skipped** (only local recognizer/model tests) |
| FDB contract/regression suite | `pytest tests/test_fdb_contracts.py tests/test_fdb_config.py tests/test_fdb_livekit_bridge.py tests/test_fdb_live_smoke_runtime.py tests/test_fdb_media.py tests/test_fdb_media_edge.py` | **52 passed** |
| Lint and types | `ruff check src tests scripts backend examples && mypy src && mypy backend` | passed, including the optional LiveKit SDK boundary |
| Package integrity | `python -m pip check && python -m build --wheel --sdist` | passed |
| FDB source drift check | `scripts/fetch_fdb.sh && python scripts/audit_fdb_contract.py ...` | pinned `3e799c45…`, **100 scenarios / 154 calls / 12 tools** validated |
| FDB bridge contract execution | `continuum-fdb-contract .../benchmark_data_v2.json` | **154 / 154** calls executed, no failures; `official_score: false` |
| Provider construction | dummy-key `continuum-fdb-agent --check start` and Gemini model import | passed; no credentials printed |
| Offline regression evaluation | `eval-arbiter`, `compare`, `ablate`, `eval-planner`, `eval-runtime`, `eval-multimodal` | all completed successfully; generated audit outputs were not committed over frozen submission reports |
| JSON fixture/report parsing | Python JSON/JSONL parse sweep | passed |
| Build/script syntax | `git diff --check`, `bash -n scripts/*.sh` | passed |
| Preview smoke | `continuum serve --host 0.0.0.0 --port 8000`; `/health`, replay, and preview-origin CORS check | passed |

The coverage measurement across `src/continuum` and `backend/app` was **73%**.
It is informational only; the project does not enforce a synthetic coverage
threshold. The exercised core state/planning/runtime modules are materially
higher, while cloud-live and CLI wrappers are necessarily lower without external
credentials.

## Reproduction command

The official, credentialed FDB-v3 path is now one command after downloading the
benchmark recordings:

```bash
export LIVEKIT_URL=... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=...
export GOOGLE_API_KEY=... OPENAI_API_KEY=...
scripts/reproduce_fdb_v3.sh /path/to/fdb_v3_data_released
```

The script performs a frozen install of `dev`, `fdb`, and `fdb-eval`, audits the
pinned upstream checkout, starts the CONTINUUM Gemini/LiveKit worker, invokes
unmodified upstream inference, then writes upstream tool-accuracy, strict-pass,
and latency reports (with `--use-llm`) plus worker/tool telemetry under
`reports/fdb-v3/<UTC-run-id>/`.

## External gates not claimable from this sandbox

- **No official FDB score was run here.** It requires the separately distributed
  100-recording dataset, a real LiveKit project, Google model access, and the
  OpenAI judge key. The local 154-call bridge run validates plumbing only.
- **The live Cloud room/media smoke was not run.** It similarly requires those
  LiveKit and Google credentials.
- **Real local ASR was not completed in this sandbox.** The multimodal packages
  installed, but downloading `Systran/faster-whisper-base.en` was interrupted by
  an external Hugging Face TLS EOF. This is a network/download limitation, not a
  passing test. On the submission machine, run
  `continuum fetch-models --asr base.en`, then
  `pytest -q -rs tests/test_real_recognition.py` and
  `continuum eval-multimodal`; the CI workflow requires this path to have no
  skips.
- **Real OCR needs an operating-system library on minimal Linux hosts.** The
  RapidOCR dependency imports GUI OpenCV, whose diagnostic error here was
  `ImportError: libGL.so.1`. The project Dockerfile already installs `libgl1`
  and `libglib2.0-0`; the README now gives the equivalent host command. This is
  a documented deployment prerequisite rather than a hidden Python failure.
- **The Docker image was not built here** because Docker is not available in the
  audit sandbox. Its package install no longer silently falls back to an
  incomplete dependency set.

These external checks should be completed and their resulting official FDB
reports/video artifacts included before final form submission. Do not replace
this audit with an inferred benchmark score.
