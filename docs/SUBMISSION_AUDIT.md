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

## Final deep-audit addendum — 30 Sep 2026

### Repository scope and latest-main check

`origin/main` was fetched immediately before this addendum. Its tip remains
`3f29167c15fb08284a49e30f49e1ebd4b353fc37`; this submission branch is based on
that exact commit and contains only the audited follow-up commits. The audit
read every one of the **200 tracked files**: **190 UTF-8 text files** were read
and parsed according to type, and all **10 binary fixtures/documents** were
validated separately. This is an audit of the complete tracked repository, not
only the Python package directory.

| Surface | Deep-audit check | Result |
|---|---|---|
| Python source/tests/scripts | AST/bytecode compilation of 111 tracked Python files; shell syntax for every tracked `.sh` | passed |
| Structured project data | Parsed 31 JSON, 3 JSONL, and both workflow YAML files | passed |
| Media/submission assets | Validated all 6 PCM WAVs, 3 PNGs, and the presentation PDF header | passed |
| Documentation | Checked 30 Markdown files for missing local link targets | passed |
| Repository integrity | `git diff --check`, `git fsck --no-reflogs --full`, and `git archive` | passed |
| Secret hygiene | High-confidence key scan of current files and all 239 reachable historical blobs | no matches |
| Dependencies | `uv lock --check`, `pip check`, isolated wheel/sdist build, and `pip-audit --strict` | passed; no known vulnerabilities reported |
| Local quality gates | `make lint`, `make test`, kit smoke, quickstart, CLI replay, FastAPI health/replay/404/CORS checks | passed |
| Locked GitHub CI | Run `36716074731` on this branch: Python 3.10/3.11/3.12, FDB contracts, and real multimodal job | all jobs passed |

The multimodal CI job now installs the required Linux `libgl1`/`libglib2.0-0`
libraries, fetches a local ASR model during setup, executes the non-skipped
real-recognition tests, and passes raw-media runtime evaluation. CI now uses
`uv sync --frozen` throughout and every third-party action is pinned to a
full commit SHA. The Docker recipe likewise installs from `uv.lock`; the new
allow-list `.dockerignore` excludes local environments, models, artifacts, and
any `.env` file from the build context. `make` now selects the project UV
virtual environment rather than accidentally invoking an unrelated system
Python after `uv sync`.

### Credential and live-service status

No secret value was read, printed, stored, or passed on a command line. The
GitHub token available to this audit can read workflow results but is not
allowed to list repository-secret names or dispatch workflows (both operations
returned GitHub HTTP 403). A prior Phase 3 workflow did pass its own required
credential-presence preflight, which establishes that its four expected secret
inputs were non-empty at that time. It subsequently failed in the live smoke
step, before this branch's diagnostic improvements.

The Phase 3 workflow now emits a **sanitized** check annotation with only the
failure stage, exception type, and failed check names; it never emits provider
exception text or credentials. Because this audit token cannot issue
`workflow_dispatch`, its credentialed re-run must be started from GitHub's
**Actions → Phase 3 live media smoke → Run workflow** UI on this branch. A
passing result is the last live-network gate.

### Final execution plan

1. **CI — complete:** retain successful run `36716074731` as evidence of the
   locked Python, FDB-contract, OCR, and real-ASR lanes.
2. **Phase 3 live smoke — owner action required:** manually dispatch the named
   workflow on `arena/01a0f211-continuum`. If it fails, read the safe
   `failure_stage` annotation and uploaded sanitized report, correct only that
   stage, and re-run until every report check is `true`.
3. **Official FDB score — credentialed evaluation environment:** provide the
   released WAV directory and all five variables, then execute
   `scripts/reproduce_fdb_v3.sh DATA_DIR`. Preserve its three upstream reports
   and sanitized worker telemetry as submission evidence.
4. **Container — environment action required:** on a machine with Docker, run
   `docker build -t continuum .` and the documented JSONL smoke. Mount a
   pre-fetched `models/faster-whisper-*` directory for raw audio. Docker is not
   installed in this audit sandbox, so that engine-level build remains an honest
   external gate.
5. **Submit only after steps 2–4:** do not represent the deterministic suite,
   FDB bridge contract, or credential-presence check as an official FDB score.
