"""
Dialogue Manager (Phase 3 — plan Task 3.3.C)

Ten canned templates keep response latency deterministic (measured, not
adjective). Selection is rule-based off the ArbiterDecision + ledger state:

  - RETRACT after COMMITTED  → honest "already booked" + cancel offer
  - RETRACT before commit    → pruned-booking confirmation, search kept
  - low confidence           → quick question (never guesses on risky ops)
  - timeout                  → "checking status…" (verify, don't blind-retry)
  - shadow promote/discard   → silent (user never sees speculative work)
"""

from __future__ import annotations

from .contracts import ArbiterCategory, ArbiterDecision

TEMPLATES: dict[str, str] = {
    "ack": "Got it — working on it.",
    "modify": "Switching to {new} — re-running only what changed; the rest stays valid.",
    "add_constraint": "Added — {value}. Everything else is kept.",
    "retract_pruned": "Understood — I've pruned the booking steps. Your search and options stay.",
    "retract_after_commit": (
        "It's already booked ({ref}). I can't silently undo an irreversible action. "
        "Want me to cancel it? I'll check cancellation terms (and fees) first."
    ),
    "cancel_offer": "I can issue a cancellation request — confirm and I'll check if it's free within 24h.",
    "new_goal": "Starting fresh for {value} — the old task is cancelled.",
    "clarify": "{question}",
    "timeout_check": "That request may have gone through — checking booking status before I retry, so nothing is double-booked.",
    "noise": "",  # silently filtered backchannel
}


def respond(event: str, **fmt: object) -> str:
    """Render a canned template; unknown events raise so tests catch typos."""
    return TEMPLATES[event].format(**fmt) if fmt else TEMPLATES[event]


def respond_to_turn(
    decision: ArbiterDecision,
    *,
    committed_ref: str | None = None,
) -> str:
    """Pick the right canned line for an applied arbiter decision.

    `committed_ref` set ⇒ the irreversible action already happened — honesty
    beats convenience: report it, offer cancel, never claim undo.
    """
    if decision.needs_clarification() and decision.suggested_clarification:
        return respond("clarify", question=decision.suggested_clarification)
    cat = decision.category
    if cat == ArbiterCategory.RETRACT:
        if committed_ref:
            return respond("retract_after_commit", ref=committed_ref)
        return respond("retract_pruned")
    if cat == ArbiterCategory.NEW_GOAL:
        return respond("new_goal", value=_new_value(decision))
    if cat == ArbiterCategory.MODIFY:
        return respond("modify", new=_new_value(decision))
    if cat == ArbiterCategory.ADD_CONSTRAINT:
        return respond("add_constraint", value=_new_value(decision))
    return respond("ack")


def _new_value(decision: ArbiterDecision) -> str:
    if decision.delta and decision.delta.new_value is not None:
        return str(decision.delta.new_value)
    return "that"
