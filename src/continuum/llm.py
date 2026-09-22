"""
LLM adapter — fused delta+arbiter single-call prompt + HTTP clients.

Phase 2: structured JSON, temperature-scaled confidence, env-gated backends.
Offline-fake remains default for CI (no network, deterministic).

Backends:
  offline-fake — table lookup (deterministic)
  dense        — MiniLM-L6-v2 centroid gate (<50ms) + pattern delta
  ollama       — local qwen3:4b via http://localhost:11434
  gemini       — gemini-2.5-flash via Generative Language API
  openai       — gpt-4o-mini via OpenAI chat completions

All backends must return ArbiterDecision; HTTP backends fallback to offline-fake
if keys/host unavailable, tagging model as "offline-fake (fallback from X)".
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from typing import Any

from .contracts import ArbiterCategory, ArbiterDecision, Delta, DeltaOp, EvidenceSpan, StateVersion

# ---------------------------------------------------------------------------
# Prompt — single fused call (delta + category + confidence + rationale)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are CONTINUUM's Intent Delta Extractor + Interrupt Arbiter.
Given prior committed state JSON, last assistant action, and new user evidence,
output STRICT JSON with:
{
  "delta": {"op": "replace"|"add"|"remove"|null, "field": str|null, "old_value": any, "new_value": any, "span": str} | null,
  "category": "NEW_GOAL" | "MODIFY" | "ADD_CONSTRAINT" | "RETRACT" | "NOISE",
  "confidence": float 0..1,
  "rationale": str (one sentence),
  "evidence_spans": [int],
  "suggested_clarification": str | null
}
Rules:
- NOISE → delta null, confidence high if backchannel.
- RETRACT/NEW_GOAL confidence <0.72 requires clarification; be conservative.
- MODIFY keeps other slots; ADD_CONSTRAINT only adds; NEW_GOAL abandons prior goal.
- Confidence is calibrated; never overstate.
"""

FEW_SHOTS = [
    {
        "state": {
            "destination": "Delhi",
            "dates": "next Monday",
            "constraints": [{"time": "morning"}],
        },
        "user": "Actually, Bangalore",
        "output": {
            "delta": {
                "op": "replace",
                "field": "destination",
                "old_value": "Delhi",
                "new_value": "Bangalore",
                "span": "Actually, Bangalore",
            },
            "category": "MODIFY",
            "confidence": 0.92,
            "rationale": "User replaced destination Delhi→Bangalore, kept other constraints",
            "evidence_spans": [0],
            "suggested_clarification": None,
        },
    },
    {
        "state": {"destination": "Delhi"},
        "user": "…but keep the morning constraint",
        "output": {
            "delta": {
                "op": "add",
                "field": "constraints",
                "old_value": None,
                "new_value": {"time": "morning"},
                "span": "keep the morning constraint",
            },
            "category": "ADD_CONSTRAINT",
            "confidence": 0.89,
            "rationale": "Add time constraint, keep existing state",
            "evidence_spans": [0],
            "suggested_clarification": None,
        },
    },
    {
        "state": {"booking": "pending"},
        "user": "Don't book it",
        "output": {
            "delta": {
                "op": "remove",
                "field": "booking_instruction",
                "old_value": "book",
                "new_value": None,
                "span": "Don't book it",
            },
            "category": "RETRACT",
            "confidence": 0.94,
            "rationale": "User retracts booking instruction",
            "evidence_spans": [0],
            "suggested_clarification": None,
        },
    },
    {
        "state": {"goal_domain": "flights"},
        "user": "Forget flights, find trains",
        "output": {
            "delta": {
                "op": "replace",
                "field": "goal_domain",
                "old_value": "flights",
                "new_value": "trains",
                "span": "Forget flights, find trains",
            },
            "category": "NEW_GOAL",
            "confidence": 0.90,
            "rationale": "New goal: abandon flights, switch to trains",
            "evidence_spans": [0],
            "suggested_clarification": None,
        },
    },
    {
        "state": {"destination": "Delhi"},
        "user": "Hmm, okay…",
        "output": {
            "delta": None,
            "category": "NOISE",
            "confidence": 0.95,
            "rationale": "Backchannel, no semantic change",
            "evidence_spans": [0],
            "suggested_clarification": None,
        },
    },
]


def build_fused_prompt(
    text: str,
    state: StateVersion | None,
    evidence: EvidenceSpan | None = None,  # noqa: ARG001
    history: list[str] | None = None,
) -> str:
    """Build prompt string for LLM backends."""
    state_json = json.dumps(state.state if state else {}, ensure_ascii=False)
    hist = "\n".join(history or [])
    shots = "\n\n".join(
        f"Example {i + 1}:\nState: {json.dumps(s['state'])}\nUser: {s['user']}\nOutput: {json.dumps(s['output'])}"
        for i, s in enumerate(FEW_SHOTS)
    )
    prior_action = hist.splitlines()[-1] if hist else "none"
    return (
        f"{SYSTEM_PROMPT}\n\nFEW-SHOT:\n{shots}\n\n"
        f"History (last action): {prior_action}\n"
        f"Current state: {state_json}\n"
        f'New user evidence: "{text}"\n'
        "Respond with STRICT JSON only, no markdown."
    )


def calibrate_confidence(raw: float, category: ArbiterCategory, temperature: float = 1.2) -> float:
    """
    Temperature scaling: logit / T -> sigmoid.
    Higher T makes confidence more conservative (lower).
    Clamped to [0,1].
    """
    raw = max(1e-6, min(1 - 1e-6, float(raw)))
    # logit
    logit = math.log(raw / (1 - raw))
    scaled_logit = logit / max(0.5, temperature)
    scaled = 1 / (1 + math.exp(-scaled_logit))
    # risky categories get extra conservatism (tiny penalty)
    if category in {ArbiterCategory.RETRACT, ArbiterCategory.NEW_GOAL} and scaled > 0.72:
        # nudge down 0.03 if barely above threshold and T >1
        if temperature > 1.0:
            scaled = max(0.0, scaled - 0.02)
    return max(0.0, min(1.0, scaled))


def parse_structured_json(raw: str) -> dict[str, Any] | None:
    """
    Parse LLM JSON output, handling markdown fences and extra text.
    Returns dict or None on failure.
    """
    s = raw.strip()
    # strip ```json fences
    if "```" in s:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", s, re.I)
        if m:
            s = m.group(1).strip()
    # try direct json
    try:
        return json.loads(s)
    except Exception:
        pass
    # fallback: find first {...} block
    m = re.search(r"\{[\s\S]*\}", s)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def _delta_from_parsed(d: dict[str, Any] | None, fallback_span: str) -> Delta | None:
    if d is None:
        return None
    op_raw = d.get("op")
    if op_raw is None:
        return None
    try:
        op = DeltaOp(op_raw)
    except Exception:
        return None
    field = d.get("field")
    if not field:
        return None
    return Delta(
        op=op,
        field=str(field),
        old_value=d.get("old_value"),
        new_value=d.get("new_value"),
        span=str(d.get("span") or fallback_span),
    )


def validation_error_to_fallback(
    raw: dict[str, Any] | None, text: str
) -> tuple[ArbiterCategory, Delta | None, float, str]:
    """Heuristic fallback when LLM JSON is invalid — map to MODIFY/NOISE."""
    low = text.strip().lower()
    if len(low.split()) <= 2 and len(low) < 12:
        return (ArbiterCategory.NOISE, None, 0.62, "LLM parse failed → heuristic noise")
    return (
        ArbiterCategory.MODIFY,
        Delta(
            op=DeltaOp.REPLACE,
            field="destination",
            old_value=None,
            new_value=text.strip()[:30],
            span=text,
        ),
        0.55,
        "LLM parse failed → heuristic modify (low confidence)",
    )


# ---------------------------------------------------------------------------
# HTTP clients — httpx, graceful fallback, no hard deps
# ---------------------------------------------------------------------------


def _httpx_post(
    url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float = 8.0
) -> tuple[int, str]:
    """Minimal httpx wrapper; falls back to urllib if httpx absent."""
    try:
        import httpx  # type: ignore

        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, headers=headers, json=payload)
            return r.status_code, r.text
    except ImportError:
        # urllib fallback (no httpx installed) — use http.client
        import urllib.error
        import urllib.request

        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=data, headers={**headers, "Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode()
                return resp.status, body  # type: ignore[attr-defined]
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(errors="ignore")
        except Exception as e:  # noqa: BLE001
            return 0, str(e)
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def call_ollama(
    prompt: str,
    model: str = "qwen3:4b",
    host: str | None = None,
    timeout: float = 8.0,
) -> tuple[str | None, int]:
    """
    Call Ollama /api/chat. Returns (raw_content, latency_ms) or (None, latency).
    Env: OLLAMA_HOST (default http://localhost:11434)
    """
    _host = host or os.getenv("OLLAMA_HOST") or "http://localhost:11434"
    url = _host.rstrip("/") + "/api/chat"
    t0 = time.perf_counter()
    status, body = _httpx_post(
        url,
        headers={"Content-Type": "application/json"},
        payload={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2, "num_predict": 400},
        },
        timeout=timeout,
    )
    lat = int((time.perf_counter() - t0) * 1000)
    if status != 200:
        return None, lat
    try:
        j = json.loads(body)
        content = j.get("message", {}).get("content") or j.get("response") or ""
        if isinstance(content, dict):
            content = json.dumps(content)
        return str(content).strip(), lat
    except Exception:
        return None, lat


def call_gemini(
    prompt: str,
    model: str = "gemini-2.5-flash",
    api_key: str | None = None,
    timeout: float = 8.0,
) -> tuple[str | None, int]:
    """
    Call Gemini generateContent. Returns (raw_content, latency_ms) or (None, latency).
    Env: GEMINI_API_KEY or GOOGLE_API_KEY
    """
    key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
    if not key:
        return None, 0
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    )
    t0 = time.perf_counter()
    status, body = _httpx_post(
        url,
        headers={"Content-Type": "application/json"},
        payload={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.2,
                "maxOutputTokens": 600,
            },
        },
        timeout=timeout,
    )
    lat = int((time.perf_counter() - t0) * 1000)
    if status != 200:
        return None, lat
    try:
        j = json.loads(body)
        cands = j.get("candidates") or []
        if not cands:
            return None, lat
        parts = cands[0].get("content", {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts)
        return text.strip() or None, lat
    except Exception:
        return None, lat


def call_openai(
    prompt: str,
    model: str = "gpt-4o-mini",
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float = 8.0,
) -> tuple[str | None, int]:
    """
    Call OpenAI chat completions. Returns (raw_content, latency_ms) or (None, latency).
    Env: OPENAI_API_KEY, OPENAI_BASE_URL
    """
    key = api_key or os.getenv("OPENAI_API_KEY") or ""
    if not key:
        return None, 0
    base = (base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    url = base + "/chat/completions"
    t0 = time.perf_counter()
    status, body = _httpx_post(
        url,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        payload={
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 600,
            "response_format": {"type": "json_object"},
        },
        timeout=timeout,
    )
    lat = int((time.perf_counter() - t0) * 1000)
    if status != 200:
        return None, lat
    try:
        j = json.loads(body)
        choices = j.get("choices") or []
        if not choices:
            return None, lat
        content = choices[0].get("message", {}).get("content") or ""
        if isinstance(content, dict):
            content = json.dumps(content)
        return str(content).strip(), lat
    except Exception:
        return None, lat


def llm_parsed_to_decision(
    parsed: dict[str, Any] | None,
    raw_latency_ms: int,
    model: str,
    fallback_text: str,
    fallback_state: StateVersion | None = None,  # noqa: ARG001
    *,
    temperature: float = 1.2,
) -> ArbiterDecision:
    """
    Validate parsed JSON into ArbiterDecision, applying calibration.
    On parse failure, uses heuristic fallback with low confidence.
    """
    t_cal = temperature
    if parsed is None:
        cat, delta, conf, rationale = validation_error_to_fallback(None, fallback_text)
        return ArbiterDecision(
            category=cat,
            confidence=calibrate_confidence(conf, cat, t_cal),
            delta=delta,
            rationale=rationale,
            evidence_spans=[0],
            suggested_clarification=None,
            latency_ms=raw_latency_ms,
            model=model,
        )
    try:
        cat_raw = parsed.get("category") or parsed.get("Category")
        cat = ArbiterCategory(str(cat_raw).upper()) if cat_raw else ArbiterCategory.MODIFY
    except Exception:
        cat = ArbiterCategory.MODIFY

    delta_dict = parsed.get("delta")
    # LLM may nest under "Delta" etc.
    delta = _delta_from_parsed(delta_dict, fallback_text) if isinstance(delta_dict, dict) else None  # noqa: SIM108

    # NOISE must have delta None
    if cat == ArbiterCategory.NOISE:
        delta = None

    raw_conf = parsed.get("confidence", 0.7)
    try:
        raw_conf_f = float(raw_conf)
    except Exception:
        raw_conf_f = 0.6

    conf = calibrate_confidence(raw_conf_f, cat, t_cal)
    rationale = str(parsed.get("rationale") or parsed.get("reason") or "LLM decision")
    spans = parsed.get("evidence_spans") or parsed.get("evidenceSpans") or [0]
    try:
        spans_list = [int(x) for x in spans] if isinstance(spans, list) else [0]
    except Exception:
        spans_list = [0]

    sugg = parsed.get("suggested_clarification") or parsed.get("suggestedClarification")

    return ArbiterDecision(
        category=cat,
        confidence=conf,
        delta=delta,
        rationale=rationale,
        evidence_spans=spans_list,
        suggested_clarification=str(sugg) if sugg else None,
        latency_ms=raw_latency_ms,
        model=model,
    )


# ---------------------------------------------------------------------------
# Dense MiniLM centroid gate (Layer 1, <50ms)
# ---------------------------------------------------------------------------

# Cache centroid vectors in-memory after first compute
_DENSE_CENTROIDS: dict[str, Any] | None = None
_DENSE_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_DENSE_THRESHOLD = 0.72

# Prototype examples for centroids (subset of gold, diverse)
_DENSE_PROTOTYPES: dict[str, list[str]] = {
    "MODIFY": [
        "Actually, Bangalore",
        "Change to evening",
        "Make it two passengers",
        "Correction, Bangalore",
        "Switch to Mumbai instead",
    ],
    "ADD_CONSTRAINT": [
        "…but keep the morning constraint",
        "Actually INR 50000 budget",
        "Only direct flights",
        "Keep morning",
        "Add baggage allowance",
    ],
    "RETRACT": [
        "Don't book it",
        "Cancel the search",
        "Never mind",
        "Abort the booking",
        "Stop booking",
    ],
    "NEW_GOAL": [
        "Forget flights, find trains",
        "Find nearby italian restaurants instead",
        "Forget this, book a hotel",
        "Switch to train search",
        "Find hospitals nearby",
    ],
    "NOISE": [
        "Hmm, okay…",
        "Okay",
        "Yeah",
        "…",
        "Hmm, yeah",
    ],
}


def _get_dense_model() -> Any | None:
    """Lazy load MiniLM; returns None if not installed/cached offline."""
    if os.getenv("CONTINUUM_DENSE_DISABLE", "0") == "1":
        return None
    allow_download = os.getenv("CONTINUUM_DENSE_DOWNLOAD", "0") == "1"
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore

        try:
            if allow_download:
                m = SentenceTransformer(_DENSE_MODEL_NAME, trust_remote_code=False)
            else:
                # offline: fail fast if not cached, no download
                m = SentenceTransformer(
                    _DENSE_MODEL_NAME, trust_remote_code=False, local_files_only=True
                )
            return m
        except Exception:
            return None
    except ImportError:
        return None


def _cosine(a: Any, b: Any) -> float:
    try:
        # Works for numpy arrays (a*b).sum() without importing numpy
        denom = (float((a * a).sum()) ** 0.5) * (float((b * b).sum()) ** 0.5)  # type: ignore[attr-defined]
        if denom == 0:
            return 0.0
        return float((a * b).sum() / denom)  # type: ignore[attr-defined]
    except Exception:
        # pure python fallback
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0


def get_dense_centroids() -> dict[str, Any] | None:
    """Compute or return cached centroids (normalized). None if model unavailable."""
    global _DENSE_CENTROIDS  # noqa: PLW0603
    if _DENSE_CENTROIDS is not None:
        return _DENSE_CENTROIDS
    model = _get_dense_model()
    if model is None:
        return None
    try:
        import numpy as np  # type: ignore

        centroids: dict[str, Any] = {}
        for cat, examples in _DENSE_PROTOTYPES.items():
            embs = model.encode(examples, normalize_embeddings=True, show_progress_bar=False)
            # embs shape (n, dim)
            centroid = np.mean(embs, axis=0)
            # normalize
            denom = float((centroid * centroid).sum()) ** 0.5
            if denom:
                centroid = centroid / denom
            centroids[cat] = centroid
        _DENSE_CENTROIDS = centroids
        return centroids
    except Exception:
        return None


def dense_classify(text: str) -> tuple[ArbiterCategory, float] | None:
    """
    Classify via cosine to centroids. Returns (category, confidence) or None if unavailable.
    Confidence is max_cosine in [0,1]; caller decides threshold.
    """
    centroids = get_dense_centroids()
    model = _get_dense_model()
    if centroids is None or model is None:
        return None
    try:
        emb = model.encode([text], normalize_embeddings=True, show_progress_bar=False)[0]
        best_cat: str | None = None
        best_score = -1.0
        for cat, cent in centroids.items():
            s = _cosine(emb, cent)
            if s > best_score:
                best_score = s
                best_cat = cat
        if best_cat is None:
            return None
        # map cosine [-1,1] -> [0,1] but already normalized positive region ~0.3-0.95
        # clamp
        conf = max(0.0, min(1.0, (best_score + 0.1)))
        return (ArbiterCategory(best_cat), float(conf))
    except Exception:
        return None


def resolve_backend(cli_backend: str | None = None) -> str:
    """
    Resolve backend preference: CLI > env (CONTINUUM_BACKEND/ARBITER_BACKEND) > offline-fake.
    Normalizes aliases: 'fake' → 'offline-fake', 'gpt' → 'openai'.
    """
    raw = (
        cli_backend
        or os.getenv("CONTINUUM_BACKEND")
        or os.getenv("ARBITER_BACKEND")
        or "offline-fake"
    )
    raw = raw.strip().lower()
    aliases = {
        "fake": "offline-fake",
        "offline": "offline-fake",
        "heuristic": "offline-fake",
        "gpt": "openai",
        "openai": "openai",
        "ollama": "ollama",
        "gemini": "gemini",
        "dense": "dense",
        "minilm": "dense",
        "offline-fake": "offline-fake",
    }
    return aliases.get(raw, raw)


__all__ = [
    "SYSTEM_PROMPT",
    "FEW_SHOTS",
    "build_fused_prompt",
    "calibrate_confidence",
    "parse_structured_json",
    "llm_parsed_to_decision",
    "call_ollama",
    "call_gemini",
    "call_openai",
    "dense_classify",
    "get_dense_centroids",
    "resolve_backend",
]
