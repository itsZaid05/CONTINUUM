"""
CONTINUUM Policy Engine
Enforces Deterministic Safety Invariants & Decision Matrix:
IRREVERSIBLE execution requires Auth == EXPLICIT AND IVS < 0.6; otherwise system must ASK or BLOCK.
"""

from typing import Literal

from backend.app.models.schemas import AuthorizationType
from backend.app.tools.registry import get_authoritative_risk

PolicyDecision = Literal[
    "EXECUTE",  # Safe to run immediately
    "EXECUTE_PRIMARY",  # Safe for primary, but don't run shadow
    "STAGE",  # Create draft / hold with rollback option
    "HOLD",  # Delay execution until user stability increases
    "ASK",  # Prompt user for explicit confirmation
    "BLOCK",  # Strictly forbid autonomous execution
]


class PolicyEngine:
    def evaluate(
        self,
        tool_name: str,
        ivs_score: float,
        authorization: AuthorizationType,
        is_shadow: bool = False,
    ) -> PolicyDecision:
        """
        Evaluates tool execution against Risk + IVS + Auth policy matrix.
        Latency target: <5ms
        """
        risk = get_authoritative_risk(tool_name)

        # Shadow Branch Invariant: Shadow branches MUST be READ-ONLY (risk == FREE)
        if is_shadow:
            if risk != "FREE":
                return "BLOCK"
            if ivs_score < 0.3:
                return "EXECUTE"
            return "BLOCK"  # Only speculate when IVS is low

        # 1. FREE Risk Tier (search_flights, search_hotels, etc.)
        if risk == "FREE":
            return "EXECUTE"

        # 2. STAGEABLE Risk Tier (hold_seat, draft_itinerary)
        if risk == "STAGEABLE":
            if ivs_score < 0.3:
                return "EXECUTE"
            elif ivs_score <= 0.6:
                return "STAGE"
            else:
                return "HOLD"

        # 3. MUTATING Risk Tier (modify_booking, cancel_booking)
        if risk == "MUTATING":
            if ivs_score < 0.3:
                return "EXECUTE"
            elif ivs_score <= 0.6:
                return "STAGE"
            else:
                return "ASK"

        # 4. IRREVERSIBLE Risk Tier (confirm_booking, process_payment)
        if risk == "IRREVERSIBLE":
            # Strict Deterministic Invariant:
            # IRREVERSIBLE actions require EXPLICIT auth AND IVS < 0.6; otherwise ASK / BLOCK
            if authorization == "EXPLICIT" and ivs_score < 0.6:
                return "EXECUTE"
            elif ivs_score >= 0.6:
                return "BLOCK"
            else:
                return "ASK"

        return "ASK"


policy_engine = PolicyEngine()
