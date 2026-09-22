"""
Policy + Commitment Control — Risk Levels

Phase 1: minimal enforcement + honest retraction message generator.
Phase 3 will add CommitGate, EffectLedger verify-after-timeout.
"""

from __future__ import annotations

from .contracts import ArbiterCategory, RiskLevel

# Node kind → RiskLevel mapping (hardcoded MVP, later policy file)
KIND_RISK: dict[str, RiskLevel] = {
    "search": RiskLevel.FREE,
    "filter": RiskLevel.FREE,
    "price": RiskLevel.FREE,
    "inform": RiskLevel.FREE,
    "hold": RiskLevel.STAGEABLE,
    "book": RiskLevel.IRREVERSIBLE,
    "pay": RiskLevel.IRREVERSIBLE,
    "cancel": RiskLevel.MUTATING,
    # price is FREE (read), but map explicitly
}


def risk_for(kind: str) -> RiskLevel:
    return KIND_RISK.get(kind, RiskLevel.FREE)


def is_mutating_or_irreversible(kind: str) -> bool:
    return risk_for(kind) in {RiskLevel.MUTATING, RiskLevel.IRREVERSIBLE}


# Dialogue for risky retraction after commit
def retraction_after_commit_message(ref: str, domain: str = "booking") -> str:
    return (
        f"It's already booked ({ref}). I can't silently undo an irreversible action. "
        f"Want me to cancel it? I'll check if {domain} supports cancellation (and fees) first."
    )


def retraction_before_commit_message() -> str:
    return "Understood — I've pruned the booking steps. Your search and options stay — want to adjust anything else?"


def low_confidence_question(category: ArbiterCategory) -> str:
    if category == ArbiterCategory.RETRACT:
        return "Do you want to cancel the booking or just change the city?"
    if category == ArbiterCategory.NEW_GOAL:
        return "Just to confirm — you want to forget the current task and start fresh — correct?"
    return "Just to confirm — did you want to change that — correct?"
