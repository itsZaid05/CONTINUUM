"""
CONTINUUM Plan DAG Generator & Speculative Shadow Predictor
Converts goals/deltas into structured DAGs and predicts read-only shadow branches (depth <= 3, max 2).
"""
from typing import List, Dict, Any, Optional
from backend.app.models.schemas import (
    PlanStep, ShadowBranch, PlanGenerateResponse, IntentClassifyResponse
)
from backend.app.tools.registry import get_authoritative_risk
from backend.app.core.version_manager import version_manager

class PlanDAGGenerator:
    def generate(
        self,
        session_id: str,
        utterance: str,
        intent: Optional[IntentClassifyResponse] = None
    ) -> PlanGenerateResponse:
        """
        Generates Primary Plan DAG + Shadow Speculative Branches.
        """
        curr_ver = version_manager.get_current_version(session_id)
        if curr_ver == 0:
            curr_ver = 1
            
        event_id = intent.event_id if intent else f"evt_v{curr_ver}"
        text = utterance.lower()

        dest = "Bangalore" if "bangalore" in text else ("Mumbai" if "mumbai" in text else "Delhi")
        slot = "morning" if "morning" in text else "anytime"

        # 1. Primary Plan DAG
        primary_plan = [
            PlanStep(
                step_id="p_1",
                tool="search_flights",
                params={"to": dest, "slot": slot},
                risk=get_authoritative_risk("search_flights"),
                depends_on=[]
            ),
            PlanStep(
                step_id="p_2",
                tool="hold_seat",
                params={"flight_id": "ref(p_1.flight_id)", "slot": slot},
                risk=get_authoritative_risk("hold_seat"),
                depends_on=["p_1"]
            ),
            PlanStep(
                step_id="p_3",
                tool="confirm_booking",
                params={"hold_id": "ref(p_2.hold_id)"},
                risk=get_authoritative_risk("confirm_booking"),
                depends_on=["p_2"]
            )
        ]

        # 2. Speculative Shadow Branches (Max 2, Depth <= 3, READ-ONLY FREE)
        shadow_branches = [
            ShadowBranch(
                branch_id="SHADOW_1",
                base_version=curr_ver,
                hypothesis=f"User will require {dest} airport transit",
                steps=[
                    PlanStep(
                        step_id="sh_1",
                        tool="search_cabs",
                        params={"to": f"{dest} Airport", "slot": slot},
                        risk=get_authoritative_risk("search_cabs"),
                        depends_on=[]
                    )
                ]
            ),
            ShadowBranch(
                branch_id="SHADOW_2",
                base_version=curr_ver,
                hypothesis=f"User will require hotel accommodation in {dest}",
                steps=[
                    PlanStep(
                        step_id="sh_2",
                        tool="search_hotels",
                        params={"city": dest},
                        risk=get_authoritative_risk("search_hotels"),
                        depends_on=[]
                    )
                ]
            )
        ]

        return PlanGenerateResponse(
            session_id=session_id,
            event_id=event_id,
            base_version=curr_ver,
            primary_plan=primary_plan,
            shadow_branches=shadow_branches
        )

plan_generator = PlanDAGGenerator()
