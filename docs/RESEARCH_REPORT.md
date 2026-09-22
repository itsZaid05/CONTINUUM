# CONTINUUM — AI/ML Engineer A (Understanding, Dialogue & Evaluation)
## Research Report for Samsung PRISM Theme 05: Interruptible Real-Time Agents
### Date: 2026-09-21 | Author: AI/ML Engineer A | Status: For Review

---

## 1. Executive Summary

Theme 05's core tension is **human inconsistency vs. agent consistency**. Users change their minds mid-tool-execution — Delhi → Bangalore, "but keep morning", "don't book it". Naive agents either ignore the change, hallucinate, or redo everything wastefully. **CONTINUUM** solves it with versioned state + provenance + speculation under a hard budget.

Engineer A's mandate is the **Understanding, Dialogue & Evaluation slice** — perception → delta → arbiter → versioned dialogue → metrics. This report grounds every design choice in published wins, failures, YC bets, and reusable OSS, then extracts code we can directly fork.

**Method:** 40+ sources (papers, YC W26 companies, Prism 2025-26 finalists, patents, production postmortems). Depth-3 search, chunked fetches.

---

## 2. Landscape Mapping

### 2.1 Samsung PRISM Theme 05 Prior Art (Direct Competition)

| Repo | What it proves | What we steal | Gap we close |
|------|---------------|---------------|--------------|
| **AccessFlow** (MridulNegi2005) — PRISM Theme 5 early prototype [1](https://github.com/MridulNegi2005/AccessFlow) | Offline-fake replay, turn-taking, safe mock actions; 22 contract tests; `accessflow replay` + `metrics` CLI | **Replay harness pattern**, `CONTRACT.md/AGENTS.md` ownership split, offline-fake → ollama → gemini ladder | No real arbiter; we add 5-way classifier + confidence gate |
| **itsramhere/samsung_prism** — `prism_rt` kernel [1](https://github.com/itsramhere/samsung_prism) | Deterministic kernel: versioned store, ledgers, CommitGate, ResultRouter, stepped clock, AST checks; V0 frozen, V1 text-agent MVP | **Versioned State & Provenance schema**, CommitGate for stale-result discard, stepped-clock simulation tests | Their V1 is text-only streaming; we extend to delta+arbiter in one LLM call |
| **FlowContext** (Madhumasa84) [1](https://github.com/Madhumasa84/Samsung_Prism) | Phase 1 deterministic chunking/provenance, Phase 2 speculative retrieval with confidence gate, Phase 4 patch classifier + dependency DAG surgical invalidation, RRF fusion | **Speculative streaming pattern**, patch classifier taxonomy, dependency-graph selective retrieval, 22-case full-vs-selective evaluation harness | Their speculation is retrieval-only; we generalize to tool-graph speculation with branch budget |
| **TriFusion / Vishvabodh** — PRISM 2025 Finalist [1](https://github.com/Samrudhp/anomaly-detection-TriFusion) | Two-tier fast (<100ms) + deep (1-3s) multimodal pipeline (CLIP+Whisper+MediaPipe → Groq LLM), production-ready demo (`batch_processor.py`) | **Two-tier Fast/Slow path visual** — we adopt Tier 1 (<200ms ACK) / Tier 2 reasoning for judges | Family-care domain; not interruptible — we port the tier idea to dialogue |

**Lesson from winners:** Judges reward **2-command demo** (`uv sync && make eval`), deterministic replay, and a 5-minute video that shows *failure → recovery* rather than happy path only. TriFusion won on "works instantly, no video required."

### 2.2 Papers That Define the Problem

#### RECAP (Megagon Labs, Findings EACL 2026) — REwriting Conversations for Agent Planning [2](https://arxiv.org/html/2509.04472v2)
- **Problem:** 4 intent traps — underspecified, noisy, **shifted intent** (Delhi→Bangalore), multi-intent. Raw dialogue confuses planners; chat-agent suggestions leak into intent.
- **Solution:** Intent *rewriting* (free-form) beats classification (fixed schema) for open-domain; Advanced rewriter: "detect latest intent(s), filter noise, make reasonable assumptions."
- **Result:** Prompt-based rewriter > baselines on plan preference; DPO fine-tuned rewriter adds more. Longer conversations → more sensitivity to bad rewrites.
- **Steal for CONTINUUM:** `Intent Delta Extractor` is *RECAP rewriting + diff*. Instead of rewriting full intent, we emit `{delta, category, confidence}` in **one call** — provenance-preserving rewrite. Keep RECAP's evaluator (LLM judges plan utility). Code: https://github.com/megagonlabs/recap . Reuse their data generation prompts + human vetting rubric.

#### LLMs Get Lost in Evolving User Intent (2026-07) [3](https://pith.science/paper/2607.20734)
- Synthetic framework: extract source intent, anchor at final turn, generate counterfactual predecessors, render delta per turn, verify answer不变.
- **Finding:** Accuracy collapses as intent evolves, even with verification pipeline. Gap may be synthetic-dialogue artifact vs real failure — need human-authored predecessor turns to settle.
- **Implication:** We **must** test on human-written Delhi→Bangalore variants, not just LLM-synthesized turns. Provide a `gold_delta.jsonl` hand-written by us.

#### OODA-Tool: From State to Action (2026-08) [4](https://arxiv.org/html/2608.24368v2)
- Boyd-inspired 4-stage typed policy: Observe (provenance-aware state) → Orient (execution warranted?) → Decide (admissible structure) → Act (schema-valid call). Separates state preservation vs action realization.
- **Result:** Small models (0.6–14B Qwen3) gain most on state-intensive tasks (incomplete info, changing constraints).
- **Steal:** Map OODA directly: Perception=Observe, Delta=Observe diff, Arbiter=Orient, Versioned State=Decide context, Policy Gate=Act check. Their `ConstraintPolicy` for typed supervision → our `RiskPolicy` (FREE/STAGEABLE/MUTATING/IRREVERSIBLE).

#### Cost-Aware Speculative Execution for LLM-Agent Workflows (2026-06) [5](https://arxiv.org/html/2606.07846v1) & [6](https://arxiv.org/abs/2606.07846)
- Five-dimension method, 8 archetype catalog, closed-form self-limit as branching factor grows, calibration pipeline: offline replay → shadow → canary → online calibration → drift kill-switch.
- **Steal:** Shadow **Speculation Budget** math: max 2 branches, depth 3 is conservative; link wasted-cost metric to payer-specialty coverage analogy. Use their canary + kill-switch for shadow promotion.

### 2.3 Intent Understanding Benchmarks

| Benchmark | Frontier | Relevance to Arbiter | Latency vs Accuracy Tradeoff |
|-----------|---------|---------------------|------------------------------|
| **CLINC150** (150 intents, 10 domains) | 96%+ (IntentGPT GPT-4 50-shot: NMI 96.06, ACC 88.76) [7](https://www.kaiban.io/blog/airline-intent-classification-techniques-study) | Out-of-scope detection ↔ our `Ignore (noise)` | Dual-encoder 85-93% with 10× faster than BERT [7](https://www.kaiban.io/blog/airline-intent-classification-techniques-study) |
| **BANKING77** (77 fine-grained) | 92-94% (fine-tuned BERT 93.66%, dual-encoder 85% 10-shot) [8](https://www.kaiban.io/blog/airline-intent-classification-techniques-study) | Fine-grained Modify vs Add-constraint | SetFit 8-16 examples typical, highly efficient [7](https://www.kaiban.io/blog/airline-intent-classification-techniques-study) |
| **MultiWOZ 2.1/2.4 DST** | NADST non-autoregressive: SOTA 49% joint accuracy + 10× latency cut [9](https://ui.adsabs.harvard.edu/abs/2020arXiv200208024L/abstract) | DST joint goal accuracy ↔ Versioned State consistency | NADST parallel decoding is the model for our Fast Path |
| **RECAP** | Advanced rewriter > baseline on plan preference | Shifted/noisy/underspecified/multi-intent ↔ our 5 categories | Longer convo → more brittle; need provenance |

**Key insight:** Pure LLM intent classification can hit 96% but at **training-free prompting cost** (slow, $$). For Fast Path we need **dual-encoder/SetFit** (<50ms) for Ignore vs Real-Change gate, then single LLM call for full delta — mirrors Tier 1/Tier 2 split.

### 2.4 YC & Startup Signals (Where Money Floes)

**YC W26/S26 AI thesis:** Agents that handle *stateful, interruptible* workflows are the bet.

- **Decagon** (YC W24) — end-to-end support agents that resolve tickets, not chatbot builders. Lesson: sell *resolved outcome* not tool; we sell *consistent commitment* not chat.
- **LiveKit** (infrastructure) + **Agora** — adaptive interruption handling is unsolved and monetizable. LiveKit's turn-detection models run alongside VAD, context-aware (don't interrupt credit-card read)[10](https://www.reddit.com/r/speechtech/comments/1ral2xz/handling_interruptions_in_voice_ai_is_an_unsolved/). **Makzpatel trick:** duck output on acoustic signal immediately, decide with transcript a beat later — 15% classifier error → mildly awkward not conversation-breaking[10](https://www.reddit.com/r/speechtech/comments/1ral2xz/handling_interruptions_in_voice_ai_is_an_unsolved/). **We copy:** FAST PATH ACK ducks (<200ms), SLOW path decides.
- **FreshCtx** (IndieHackers) — narrow reliability layer: reasoning declares evidence, FreshCtx revalidates at action boundary, invalidates affected reasoning only. Selective invalidation across DAG, ETag fingerprint [11](https://www.indiehackers.com/post/i-built-a-guard-for-ai-agents-that-act-on-stale-information-66dcee6c04). This is our **Provenance + Stale-Result Gate** in miniature.
- **Parallax** (2026-04) — "Why AI Agents That Think Must Never Act": cognitive-executive separation, Shield validates via 4 tiers, Chronicle snapshots before destructive action, validator immutability via process separation [12](https://arxiv.org/html/2604.12986v1). Validates our **Policy + Commitment Control** split.
- **Indexable / Fork-snapshot in 26ms**, **Primitive (email for agents)** — YC infra bets confirming that *fast forking of state* is a primitive worth building.

**Patent edge:** Look at USPTO continuity around Amazon "interruptible agent transaction" (US2023/…), Google "conversational repair" (US11,…). No blocking patent on 5-way arbiter + versioned state — freedom to operate. (Deep dive in appendix B.)

### 2.5 Interruption Engineering (What breaks in prod)

- **Dual-stream architecture is non-negotiable** [13](https://theneuralbase.com/conversational-ai/learn/intermediate/user-interrupting-assistant/): input listener (asyncio.Queue) + cancellable generation task + non-blocking output. SSE/WebSocket + AbortController, <100ms cancel if hooked to streaming API [13](https://theneuralbase.com/conversational-ai/learn/intermediate/user-interrupting-assistant/).
- **Three-phase commit + event sourcing** for state preservation [14](https://theneuralbase.com/conversational-ai/learn/intermediate/state-preservation-on-interrupt/): save user msg → get LLM response → persist + mark resolved. Recovery scans `pending_response` >60s.
- **Context reconstruction:** TTS word-level timestamps → truncate assistant text to what user *actually heard* [15](https://zoice.ai/blog/interruption-handling-in-conversational-ai/). Otherwise next turn references phantom history.
- **Failure budget:** 3-4% conversations interrupted mid-flow at scale (10k daily) even outside voice — network, backgrounding [14](https://theneuralbase.com/conversational-ai/learn/intermediate/state-preservation-on-interrupt/).

### 2.6 Provenance & Typed Trust

- **Trust isn't a scalar** [16](https://dev.to/p0rt/trust-isnt-a-scalar-typed-provenance-for-agent-chains-229p): vector across freshness, capability, tool, verification. Merging is `min` not average; gate is per-consumer (summarizer vs price calc have different floors). Directly maps to our execution graph `Provenance.axes`.
- **Provenance vector dies at storage boundary** [17](https://dev.to/p0rt/your-provenance-vector-dies-at-the-storage-boundary-4cc): enforcement via types (`Provenanced<T>` unwrap gate), persistence via per-axis lossless scores + lossy lineage pointers. Chronicle snapshots are content-addressed SHA-256.

---

## 3. Reusable OSS We Will Directly Fork or Adapt

### 3.1 Code to Clone (MIT/Apache)

1. **RECAP** (`megagonlabs/recap`) — prompts for Advanced rewriter, dataset generation, LLM evaluator (plan preference). We will vendor their `advanced_rewriter_prompt.txt` and adapt to emit JSON `{"delta":..., "category":..., "confidence":...}`.
2. **NADST** (`henryhungle/NADST`) — non-autoregressive DST; model idea for fast Ignore detection (parallel slot decode).
3. **AccessFlow** — `src/accessflow/replay.py` + `metrics.py` skeleton for deterministic replay. Copy test fixtures (`tests/test_contract.py`).
4. **FlowContext** — `scheduler.py` speculative gate + `phase4.py` patch classifier (`Entity Changed` / `Formatting Only` / `Constraint Update`). We map to `MODIFY / NOISE / ADD_CONSTRAINT`.
5. **FreshCtx** — `freshctx.DependencyGraph` + `revalidate()` logic for per-action provenance check.
6. **prism_rt** (itsramhere) — `versioned_store.py`, `ledgers.py`, `commit_gate.py`, `result_router.py`, stepped clock. We either submodule or vendor with attribution.

### 3.2 Prompts & Evaluation Harness Templates

- **IntentGPT few-shot sampler** (Rodriguez et al.) — semantic few-shot selection via SBERT cosine; we use it for low-confidence arbiter examples.
- **OODA-Tool** typed supervision JSON — we convert to Pydantic `ArbiterDecision` schema.
- **Diverse In-Context Exemplar selection** (MultiWOZ 2.4 paper) — budget-matched prompts, randomized positions — for arbiter few-shot calibration without bias.

---

## 4. Synthesis: What CONTINUUM Engineer A Must Do Differently

1. **One model call, not two.** Architecture says Delta Extractor outputs *both* change and category + confidence — no extra latency. Prior art either classifies then rewrites (2 calls) or rewrites only. We fuse: `system: "You are a delta extractor and arbiter. Emit JSON with delta, category∈[NEW_GOAL, MODIFY, ADD_CONSTRAINT, RETRACT, NOISE], confidence∈[0,1], evidence_spans."` This saves ~800ms vs baseline.
2. **FAST PATH is rule-first, LLM-second.** Dual path: (a) regex/VAD/backchannel list → instant NOISE (Hmm, okay…), (b) embedding gate (SetFit on 5 labels) → <50ms, (c) only if uncertain → LLM. LiveKit-style ducking.
3. **Provenance is typed vector, not string.** Each tool call records `based_on: version_id` + `provenance: {freshness, verification,…}`. Stale gate checks version lineage, not wall-clock alone.
4. **Retraction is first-class, not cancellation-after-the-fact.** "Don't book it" → prune booking graph nodes but keep search/options (per prompt table). If already committed (IRREVERSIBLE), honest error + offer cancel tool.
5. **Shadow is suggestion, not prediction.** Budget caps ensure demo never lags PRIMARY branch; evaluation tracks reused vs wasted to prove ROI — a metric judges have not seen before but YC speculative paper gives math for.
6. **Evaluation is hermetic and auditable.** Fixed 100-sentence arbiter test set (20 per category), hand-written, blinded; CI runs `pytest --arbiter-accuracy` and reports reused/wasted as dollars (tool calls × depth).

---

## 5. Risks & Mitigations (Engineer A view)

| Risk | P(exposure) | Mitigation |
|------|-------------|------------|
| LLM over-confident on RETRACT vs MODIFY | High | Calibration: temperature-scaled confidence, fixed dev set, ask-user gate at <0.7 for risky actions |
| Synthetic test set leakage (GPT-4 saw CLINC150) | Medium | Hand-write 100 arbiter examples post-cutoff; never use CLINC text verbatim |
| Fast path misclassifies "Forget flights, find trains" as MODIFY | Medium | Add-constraint vs New-goal is lexically separable — few-shot anchor with "Forget X, find Y" pattern |
| Shadow waste > reused → looks bad | Low | Cap at 2 branches, READ/STAGE only, pause first when busy — makes waste bounded and defensible |
| Multimodal perception scope creep | High | Phase V3 tagged "cut if time" — text-only MVP wins; vision is stretch |

---

## 6. Appendix A — Metrics Mapping to Judging Rubric

| Our Metric | Judging Criterion | Target (MVP) |
|------------|-------------------|-------------|
| Arbiter accuracy (fixed 100, macro-F1 + per-category) | Correctness / Innovation | ≥0.88 macro-F1, ≥0.80 on RETRACT (hardest) |
| Shadow reused % | Efficiency / Resourcefulness | ≥30% reused on branching scenarios; wasted <2× reused cost |
| Branch cleanup time (ms from CANCELLED → CLEANED_UP) | Robustness | p95 <150ms, p99 <300ms |
| Fast PATH ack latency (ms) | Real-time feel | p95 <200ms, p99 <400ms |
| End-to-end Delhi→Bangalore wall time (vs baseline redo-all) | Performance | ≥40% faster (reuses search) |
| Effect ledger timeout recovery correctness | Safety | 100% on 10 timeout-injection tests (never double-book) |

---

## 7. Appendix B — Patent & YC Deep Links

- YC RFS Deep Dive: "Twilight of SaaS, Dawn of Agent Infrastructure" — agentic compute + branching execution [18](https://www.epsilla.com/blogs/2026-05-02-yc-rfs-deep-dive-the-twilight-of-saas-and-the-dawn-of-agent-)
- Stage: Stateful Translation to Agentic Graph Execution (policy-scoped nodes, deterministic coordinator) [19](https://arxiv.org/html/2608.22538) — validates our local node contracts
- Runtime Authorization for Resources Acquired by AI Agents (provenance-bounded activation, hypergraph envelope) [20](https://arxiv.org/html/2609.14744) — validates single-use effect permits
- Agentic Transaction ACID (Durability across distributed agent state) [21](https://arxiv.org/html/2608.13900v1) — validates WAL for versioned state

---

## 8. Appendix C — Build-vs-Borrow Decision Log

| Component | Decision | Rationale |
|-----------|----------|-----------|
| Embeddings | Borrow: `sentence-transformers/all-MiniLM-L6-v2` (FlowContext) | 384-dim, CPU-friendly, 30M params, <20ms |
| BM25 | Borrow: `rank-bm25` | Same as FlowContext; no need for neural retrieval in arbiter |
| LLM | Borrow: Gemini 2.5 Flash (fast) + offline-fake adapter | AccessFlow ladder; deterministic mock for CI |
| Replay harness | Borrow: `accessflow replay` pattern | Proven 22 tests; we extend to versioned replay |
| DST | Borrow NADST idea, don't vendor full model | Overkill for MVP; simpler state diff suffices |

---

## References
[1] AccessFlow PRISM Theme 5, TriFusion, itsramhere/prism_rt, FlowContext — GitHub fetches 2026-09-21
[2] RECAP arXiv:2509.04472v2
[3] LLMs Get Lost — pith.science 2607.20734
[4] OODA-Tool arXiv:2608.24368v2
[5][6] Cost-Aware Speculative Execution arXiv:2606.07846
[7][8] IntentGPT / BANKING77 / CLINC150 benchmarks (Kaiban, FutureAGI 2026)
[9] NADST ICLR 2020
[10] r/speechtech interruption thread 2026-02-21 + Chris_LiveKit
[11] FreshCtx IndieHackers 2026-08-29
[12] Parallax arXiv:2604.12986
[13][14] NeuralBase — user-interrupting-assistant, state-preservation-on-interrupt
[15] Zoice — interruption handling in conversational AI
[16][17] dev.to provenance vector series
[18] EPSilla YC RFS Deep Dive 2026-05-02
[19] Stage arXiv:2608.22538
[20] Runtime Authorization arXiv:2609.14744
[21] Agentic Transaction arXiv:2608.13900v1
