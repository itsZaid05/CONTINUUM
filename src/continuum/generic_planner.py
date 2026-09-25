"""
Manifest-driven generic planner (AI/ML Engineer B).

``planner.generate_plan`` encodes one domain (search -> hold -> confirm for
flights). The Theme 05 harness hands the agent *tool manifests* per scenario,
including tools never seen at build time, so this planner derives everything
from the manifests themselves:

1. **Tool ranking.** Every manifest is scored against the turn:
     topic overlap   utterance concepts vs the tool's name / keywords /
                     description (synonyms folded: "complaint" ~ "ticket")
     slot evidence   argument values the turn supplies for *this* tool's
                     schema (an error code, an enum value, a date...)
     verb class      read / create / update / cancel of the turn vs the tool;
                     an information question never selects a state-changing tool
     continuity      the goal already in progress (corrections stay on it)
     negation        "don't book it" / "forget flights" push those tools down
2. **Goal handling.** new goal · correction of the active goal · additive
   ("also find a hotel") · retraction (keep the read-only prefix of the chain,
   drop every state-changing step) · backchannel no-op.
3. **Chaining.** A required argument nobody supplied is produced by another
   tool whose manifest ``returns`` that field (``hold_id`` <- ``hold_seat``
   <- ``flight_id`` <- ``search_flights``), bound as ``ref(step.field)``.
   Only READ_ONLY / STAGEABLE tools are used as producers — a plan never
   commits an effect just to obtain an argument.
4. **Gating.** IRREVERSIBLE steps need an explicit commit verb in the turn
   ("book", "order", "delete", "go ahead") or a later confirmation;
   MUTATING / STAGEABLE steps run when the turn asks for a change. Missing
   required arguments become a clarification question, never a guess.

The arbiter's *category* (NOISE / RETRACT / NEW_GOAL) is used; its slot delta
is not — the offline arbiter is travel-specific and writes whole out-of-domain
sentences into ``destination``. Slots come from ``slots.SlotExtractor``.

Deterministic, no model call, sub-millisecond per turn.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .contracts import ArbiterCategory, ArbiterDecision, RiskLevel
from .perception import _is_backchannel
from .planner import PlanStep
from .slots import STOPWORDS, Extraction, SlotExtractor, lookup_slot, set_slot, words
from .tools import ToolManifest, ToolRegistry

# ---------------------------------------------------------------------------
# Lexicon
# ---------------------------------------------------------------------------

_VERBS = {
    "read": "find search look show list check see tell lookup compute fetch view browse run "
    "display locate query",
    "create": "book reserve open create file raise log report submit schedule add order buy "
    "purchase pay confirm start hold remind navigate place",
    "update": "change update modify move reschedule switch set turn increase decrease lower "
    "adjust escalate",
    "cancel": "cancel delete remove close drop stop",
}
VERB_CLASS: dict[str, str] = {w: cls for cls, ws in _VERBS.items() for w in ws.split()}
WRITE_CLASSES = {"create", "update", "cancel"}
_PHRASE_VERBS = {
    r"\btake me\b": "create",
    r"\bmake it\b": "update",
    r"\blook up\b": "read",
    r"\bset up\b": "create",
    r"\bgo ahead\b": "create",
}
COMMIT_WORDS = {"book", "confirm", "pay", "purchase", "buy", "order", "reserve", "delete",
                "remove", "schedule", "place"}  # fmt: skip
_DETERMINERS = {"a", "an", "the", "any", "my", "your", "some", "no", "that", "this"}
_QUESTION_START = {"what", "whats", "what's", "where", "when", "which", "who", "how", "is",
                   "are", "am", "does", "do", "did", "will", "any", "has", "have"}  # fmt: skip

_LEMMA = {
    "flights": "flight", "booking": "book", "bookings": "book", "booked": "book",
    "reservation": "reserve", "reservations": "reserve", "reserved": "reserve",
    "parking": "park", "charger": "charge", "charging": "charge", "chargers": "charge",
    "navigation": "navigate", "directions": "direction", "meetings": "meeting",
    "warmer": "warm", "colder": "cold", "cooler": "cool", "hotter": "hot",
    "restaurants": "restaurant", "events": "event", "diagnostics": "diagnostic",
    "tickets": "ticket", "complaints": "complaint", "parts": "part", "hotels": "hotel",
    "cabs": "cab", "manuals": "manual", "scheduled": "schedule", "scheduling": "schedule",
    "ordered": "order", "created": "create", "cancelled": "cancel", "canceled": "cancel",
    "degrees": "degree", "technicians": "technician", "options": "option",
}  # fmt: skip
# Concept folding: user vocabulary -> the word a manifest is likely to use.
_CONCEPT_SETS = {
    "flight": "flight fly plane airline",
    "hotel": "hotel stay room accommodation lodging",
    "cab": "cab taxi ride uber",
    "ticket": "ticket complaint",
    "route": "route navigate direction drive take way",
    "event": "event meeting appointment remind reminder",
    "restaurant": "restaurant food eat dinner lunch",
    "manual": "manual guide instruction mean",
    "repair": "repair fix technician visit engineer",
    "part": "part spare replacement",
    "park": "park",
    "charge": "charge ev battery",
    "temperature": "temperature degree ac climate warm cool cold hot heat",
    "weather": "weather forecast rain sunny",
    "calendar": "calendar free available busy",
    "status": "status progress",
    "priority": "priority urgent escalate",
    "book": "book reserve",
}
CONCEPT: dict[str, str] = {w: c for c, ws in _CONCEPT_SETS.items() for w in ws.split()}


def lemma(w: str) -> str:
    w = w.lower().strip("'")
    if w in _LEMMA:
        return _LEMMA[w]
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def concept(w: str) -> str:
    lw = lemma(w)
    return CONCEPT.get(lw, lw)


# ---------------------------------------------------------------------------
# Turn cues
# ---------------------------------------------------------------------------


@dataclass
class Cues:
    concepts: set[str]
    verbs: set[str]
    negated: set[str]  # concepts / verb lemmas the user negated
    question: bool
    request: bool  # "can you / can someone ..." — asks for an action, not information
    correction: bool
    additive: bool
    replace: bool
    commit: bool


def parse_cues(text: str) -> Cues:
    low = text.lower().strip()
    toks = words(low)
    negated: set[str] = set()
    for m in re.finditer(
        r"\b(?:don't|dont|do not|never|not|no need to|forget(?: about)?|instead of|rather than|"
        r"without|no more)\s+([^,.;!?]{1,40})",
        low,
    ):
        for w in words(m.group(1))[:3]:
            negated.add(lemma(w))
            negated.add(concept(w))
    verbs: set[str] = set()
    for i, w in enumerate(toks):
        lw = lemma(w)
        if lw in VERB_CLASS and lw not in negated and (i == 0 or toks[i - 1] not in _DETERMINERS):
            verbs.add(VERB_CLASS[lw])
    for pat, cls in _PHRASE_VERBS.items():
        if re.search(pat, low):
            verbs.add(cls)
    request = bool(re.match(r"^(?:please\s+)?(?:can|could|would|will)\s+(?:you|someone|we|u)\b", low))
    question = not request and bool(toks) and (toks[0] in _QUESTION_START or low.endswith("?"))
    concepts = {
        concept(w) for w in toks if w not in STOPWORDS and concept(w) not in negated
    }
    return Cues(
        concepts=concepts,
        verbs=verbs,
        negated=negated,
        question=question,
        request=request,
        correction=bool(
            re.match(r"^\s*(?:actually|no\b|nope|wait|sorry|rather|make it|change|what about|how about)", low)
            or re.search(r"\b(?:actually|instead)\b", low)
        ),
        additive=bool(re.search(r"\b(?:also|as well|too|another)\b", low)),
        replace=bool(re.search(r"\b(?:forget|never mind|nevermind|scratch that|start over)\b", low)),
        commit=any(lemma(w) in COMMIT_WORDS and lemma(w) not in negated for w in toks)
        or "go ahead" in low,
    )


# ---------------------------------------------------------------------------
# Tool profile
# ---------------------------------------------------------------------------


def tool_class(m: ToolManifest) -> str:
    if not m.state_changing:
        return "read"
    for tok in [*m.name.split("_"), *words(m.description)[:1]]:
        cls = VERB_CLASS.get(lemma(tok))
        if cls in WRITE_CLASSES:
            return cls
    return "create"


def tool_profile(m: ToolManifest) -> dict[str, float]:
    prof: dict[str, float] = {}

    def add(word: str, weight: float) -> None:
        c = concept(word)
        if c and c not in STOPWORDS:
            prof[c] = max(prof.get(c, 0.0), weight)

    for tok in m.name.split("_"):
        if lemma(tok) not in VERB_CLASS:
            add(tok, 2.0)
    for kw in m.keywords:
        add(kw, 1.5)
    for w in words(m.description):
        if w not in STOPWORDS and lemma(w) not in VERB_CLASS:
            add(w, 1.0)
    if m.domain and m.domain != "generic":
        add(m.domain, 1.0)
    return prof


def _tool_words(m: ToolManifest) -> set[str]:
    out = {lemma(t) for t in m.name.split("_")} | {concept(t) for t in m.name.split("_")}
    out |= {lemma(k) for k in m.keywords} | {concept(k) for k in m.keywords}
    return out


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


@dataclass
class PlanDecision:
    action: str  # plan | clarify | noop
    mode: str  # new | modify | additive | retract | answer | noop
    goals: list[str]
    steps: list[PlanStep]
    slots: dict[str, Any]
    changed_slots: dict[str, Any] = field(default_factory=dict)
    missing: list[tuple[str, str]] = field(default_factory=list)
    needs_confirmation: list[str] = field(default_factory=list)
    authorized: list[str] = field(default_factory=list)
    goal_of: dict[str, str] = field(default_factory=dict)  # step_id -> goal
    question: str | None = None
    scores: dict[str, float] = field(default_factory=dict)
    alternatives: dict[str, list[Any]] = field(default_factory=dict)  # "tool.arg" -> other readings
    latency_ms: float = 0.0

    @property
    def tools(self) -> list[str]:
        return [s.tool for s in self.steps]

    def args_for(self, tool: str) -> dict[str, Any]:
        for s in self.steps:
            if s.tool == tool:
                return dict(s.params)
        return {}

    @property
    def state_changing(self) -> bool:
        return any(s.risk != RiskLevel.FREE for s in self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "mode": self.mode,
            "goals": self.goals,
            "steps": [s.to_dict() for s in self.steps],
            "slots": self.slots,
            "changed_slots": self.changed_slots,
            "missing": [list(x) for x in self.missing],
            "needs_confirmation": self.needs_confirmation,
            "authorized": self.authorized,
            "question": self.question,
            "scores": {k: round(v, 2) for k, v in self.scores.items()},
            "alternatives": self.alternatives,
            "latency_ms": round(self.latency_ms, 3),
        }


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

STRONG = 1.5  # minimum score for a tool to be read as the turn's goal
MAX_CHAIN_DEPTH = 3


class GenericPlanner:
    """Stateless per call: session memory (slots, goals) is passed in and returned."""

    def __init__(self, registry: ToolRegistry, *, today: date | None = None) -> None:
        self.registry = registry
        self.extractor = SlotExtractor(today)

    # ------------------------------------------------------------------ rank
    def rank(
        self,
        text: str,
        cues: Cues,
        ex: dict[str, dict[str, Extraction]],
        slots: dict[str, Any],
        active_chain: set[str],
        committed: set[str],
    ) -> dict[str, float]:
        scores: dict[str, float] = {}
        for m in self.registry.all():
            prof = tool_profile(m)
            topic = sum(prof[c] for c in cues.concepts if c in prof)
            evidence = min(2.5, sum(e.evidence for e in ex[m.name].values()))
            cls = tool_class(m)
            if cues.request and not cues.verbs:
                verb = 0.5  # an action is asked for, but not which kind
            elif cls == "read":
                verb = 1.0 if (not cues.verbs or "read" in cues.verbs or cues.question) else -0.5
            elif cls in cues.verbs:
                verb = 2.0
            elif cues.verbs & WRITE_CLASSES:
                verb = 0.5
            elif cues.question or "read" in cues.verbs:
                verb = -3.0  # an information request never selects a write tool
            else:
                verb = 0.0
            neg = 0.0
            if _tool_words(m) & cues.negated:
                neg = -5.0 if m.state_changing else -3.0
            cont = 0.0
            if m.name in active_chain and not cues.replace:
                cont = 1.5
            if m.name in committed and m.state_changing:
                cont = -3.0  # re-running a committed effect would duplicate it
            missing = sum(
                1
                for p in m.required
                if p not in ex[m.name]
                and lookup_slot(slots, p) is None
                and not self._producer(p, m, set())
                and not self.extractor.is_free_text(p, m)
            )
            scores[m.name] = topic + evidence + verb + neg + cont - 0.3 * missing
        return scores

    # ------------------------------------------------------------------ plan
    def plan(
        self,
        text: str,
        *,
        decision: ArbiterDecision | None = None,
        slots: dict[str, Any] | None = None,
        goals: list[str] | None = None,
        committed: set[str] | None = None,
        authorized: set[str] | None = None,
        pending: tuple[str, str] | None = None,
    ) -> PlanDecision:
        t0 = time.perf_counter()
        slots = dict(slots or {})
        goals = [g for g in (goals or []) if self.registry.has(g)]
        committed = set(committed or ())
        authorized = set(authorized or ())

        if _is_backchannel(text) or (
            decision is not None
            and decision.category == ArbiterCategory.NOISE
            and decision.confidence >= 0.9
        ):
            return self._finish(t0, PlanDecision("noop", "noop", goals, [], slots))

        cues = parse_cues(text)
        ex = {m.name: self.extractor.extract(text, m) for m in self.registry.all()}
        active = [g for g in goals if g not in committed]
        active_chain = {t for g in active for t in self._chain_tools(g)}
        scores = self.rank(text, cues, ex, slots, active_chain, committed)
        best = max(scores, key=lambda k: scores[k]) if scores else None
        strong = best is not None and scores[best] >= STRONG
        if best is not None and not strong and len(self.registry.all()) == 1:
            strong = scores[best] > 0  # a one-tool registry: any compatible request is for it

        retract = cues.negated & (
            {lemma(w) for w in COMMIT_WORDS} | {"book", "reserve", "confirm", "order", "pay"}
        ) or (
            decision is not None
            and decision.category == ArbiterCategory.RETRACT
            and decision.confidence >= 0.72
            and not (strong and best is not None and tool_class(self.registry.get(best)) == "cancel")
        )
        new_turn_goals: set[str] = set()

        new_topic = (
            strong and best is not None and pending is not None
            and best not in self._chain_tools(pending[0]) and scores[best] >= STRONG + 1.0
        )
        if pending is not None and self.registry.has(pending[0]) and not new_topic:
            mode, new_goals = "answer", [pending[0]]
            self._answer_pending(text, pending, ex, slots)
        elif retract and goals:
            # includes goals whose effect already landed: the runtime answers
            # those honestly ("already booked") instead of pretending to undo
            mode, new_goals = "retract", []
            for g in active:
                prefix = self._read_prefix(g)
                if prefix and prefix not in new_goals:
                    new_goals.append(prefix)
        elif not strong:
            if not active:
                return self._finish(
                    t0,
                    PlanDecision(
                        "clarify", "noop", goals, [], slots,
                        question="Sorry, what would you like me to do?", scores=scores,
                    ),
                )
            mode, new_goals = "modify", list(active)
        else:
            assert best is not None
            evidence_on_active = any(ex[t] for t in active_chain)
            if best in active_chain or (cues.correction and active and evidence_on_active and not cues.replace):
                mode, new_goals = "modify", list(active)
            elif active and any(g in self._chain_tools(best) for g in active):
                mode = "modify"  # upgrade: "book it" while its search is running
                new_goals = [best] + [g for g in active if g not in self._chain_tools(best)]
                new_turn_goals.add(best)
            elif cues.additive and active and not cues.replace:
                mode, new_goals = "additive", [*active, best]
                new_turn_goals.add(best)
            else:
                mode, new_goals = "new", [best]
                new_turn_goals.add(best)

        # ---- build chains, bind args, collect missing ----
        steps: list[PlanStep] = []
        goal_of: dict[str, str] = {}
        missing: list[tuple[str, str]] = []
        changed: dict[str, Any] = {}
        alternatives: dict[str, list[Any]] = {}
        for g in new_goals:
            for step in self._build_chain(
                text, g, g, ex, slots, missing, changed, alternatives,
                fresh=g in new_turn_goals, depth=0, visited=set(),
            ):
                if step.step_id not in goal_of:
                    steps.append(step)
                    goal_of[step.step_id] = g
        for k, v in changed.items():
            set_slot(slots, k, v)

        if cues.commit and (new_turn_goals or mode == "modify"):
            authorized |= set(new_goals)
        authorized &= set(new_goals)
        needs_conf = [
            s.tool for s in steps if s.risk == RiskLevel.IRREVERSIBLE and goal_of[s.step_id] not in authorized
        ]
        question = None
        action = "plan"
        if missing:
            action = "clarify"
            tool, arg = missing[0]
            question = self._question(tool, arg)
        return self._finish(
            t0,
            PlanDecision(
                action, mode, new_goals, steps, slots,
                changed_slots=changed, missing=missing, needs_confirmation=needs_conf,
                authorized=sorted(authorized), goal_of=goal_of, question=question,
                scores=scores, alternatives=alternatives,
            ),
        )

    def replan(
        self,
        goals: list[str],
        slots: dict[str, Any],
        *,
        authorized: set[str] | None = None,
    ) -> PlanDecision:
        """Rebuild the plan for known goals from memory alone (no new utterance):
        used when a confirmation is declined or a goal set is edited."""
        t0 = time.perf_counter()
        slots = dict(slots)
        goals = [g for g in goals if self.registry.has(g)]
        ex: dict[str, dict[str, Extraction]] = {m.name: {} for m in self.registry.all()}
        steps: list[PlanStep] = []
        goal_of: dict[str, str] = {}
        missing: list[tuple[str, str]] = []
        for g in goals:
            for step in self._build_chain(
                "", g, g, ex, slots, missing, {}, {}, fresh=False, depth=0, visited=set()
            ):
                if step.step_id not in goal_of:
                    steps.append(step)
                    goal_of[step.step_id] = g
        auth = set(authorized or ()) & set(goals)
        needs = [s.tool for s in steps if s.risk == RiskLevel.IRREVERSIBLE and goal_of[s.step_id] not in auth]
        return self._finish(
            t0,
            PlanDecision(
                "clarify" if missing else "plan", "modify", goals, steps, slots,
                missing=missing, needs_confirmation=needs, authorized=sorted(auth), goal_of=goal_of,
                question=self._question(*missing[0]) if missing else None,
            ),
        )

    def read_prefix(self, goal: str) -> str | None:
        return self._read_prefix(goal)

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _finish(t0: float, d: PlanDecision) -> PlanDecision:
        d.latency_ms = (time.perf_counter() - t0) * 1000.0
        return d

    def _producer(self, field_name: str, consumer: ToolManifest, visited: set[str]) -> ToolManifest | None:
        """Best READ_ONLY/STAGEABLE tool that returns ``field_name``."""
        cands = [
            p
            for p in self.registry.producers_of(field_name)
            if p.name != consumer.name
            and p.name not in visited
            and p.risk in {RiskLevel.FREE, RiskLevel.STAGEABLE}
        ]
        if not cands:
            return None
        cands.sort(
            key=lambda p: (p.domain != consumer.domain, p.risk != RiskLevel.FREE, len(p.required), p.name)
        )
        return cands[0]

    def _chain_tools(self, goal: str, depth: int = 0, visited: set[str] | None = None) -> list[str]:
        visited = set(visited or ()) | {goal}
        m = self.registry.get(goal)
        out: list[str] = []
        if depth < MAX_CHAIN_DEPTH:
            for p in m.required:
                prod = self._producer(p, m, visited)
                if prod is not None:
                    for t in self._chain_tools(prod.name, depth + 1, visited):
                        if t not in out:
                            out.append(t)
        return [*out, goal]

    def _read_prefix(self, goal: str) -> str | None:
        """Retraction keeps the deepest read-only tool of the goal's chain."""
        reads = [t for t in self._chain_tools(goal) if not self.registry.get(t).state_changing]
        return reads[-1] if reads else None

    def _build_chain(
        self,
        text: str,
        tool: str,
        goal: str,
        ex: dict[str, dict[str, Extraction]],
        slots: dict[str, Any],
        missing: list[tuple[str, str]],
        changed: dict[str, Any],
        alternatives: dict[str, list[Any]],
        *,
        fresh: bool,
        depth: int,
        visited: set[str],
    ) -> list[PlanStep]:
        m = self.registry.get(tool)
        visited = visited | {tool}
        args: dict[str, Any] = {}
        deps: list[str] = []
        pre: list[PlanStep] = []
        is_goal = tool == goal
        for p in m.properties:
            hit = ex[tool].get(p)
            if hit is not None:
                args[p] = hit.value
                changed[p] = hit.value
                if hit.alternatives:
                    alternatives[f"{tool}.{p}"] = list(hit.alternatives)
                continue
            if self.extractor.is_free_text(p, m) and is_goal:
                current = slots.get(p)
                if fresh or current is None:
                    ft = self.extractor.free_text(text, p, m)
                    if ft:
                        args[p] = ft
                        changed[p] = ft
                        continue
                if current is not None:
                    args[p] = current
                continue
            prod = self._producer(p, m, visited) if p in m.required else None
            if prod is not None and depth < MAX_CHAIN_DEPTH:
                pre += self._build_chain(
                    text, prod.name, goal, ex, slots, missing, changed, alternatives,
                    fresh=fresh, depth=depth + 1, visited=visited,
                )
                args[p] = f"ref({prod.name}.{p})"
                deps.append(prod.name)
                continue
            v = lookup_slot(slots, p)
            if v is not None and not (isinstance(v, str) and v.startswith("ref(")):
                args[p] = v
                if p not in slots:
                    changed[p] = v  # inherited through an alias: memory records what was used
        for p in m.required:
            if p not in args:
                missing.append((tool, p))
        step = PlanStep(
            step_id=tool,
            tool=tool,
            kind=m.kind,
            params=args,
            depends_on=deps,
            risk_level=m.risk,
        )
        out: list[PlanStep] = []
        for s in [*pre, step]:
            if all(s.step_id != o.step_id for o in out):
                out.append(s)
        return out

    def _answer_pending(
        self,
        text: str,
        pending: tuple[str, str],
        ex: dict[str, dict[str, Extraction]],
        slots: dict[str, Any],
    ) -> None:
        tool, arg = pending
        m = self.registry.get(tool)
        if arg in ex[tool]:
            return  # typed extraction already found it; _build_chain binds it
        if self.extractor.is_free_text(arg, m):
            value = self.extractor.free_text(text, arg, m) or text.strip().rstrip("?.!")
            set_slot(slots, arg, value)

    def _question(self, tool: str, arg: str) -> str:
        m = self.registry.get(tool)
        label = arg.replace("_", " ")
        if self.extractor.is_free_text(arg, m):
            return f"Could you tell me the {label}?"
        return f"Which {label} should I use?"

