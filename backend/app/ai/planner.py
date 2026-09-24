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
    def __init__(self):
        # Multi-turn session domain tracking (flight, hotel, cab)
        self.session_domains: Dict[str, str] = {}

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

        dest = "Bangalore"
        origin = "Delhi"
        slot = "morning" if "morning" in text else "anytime"
        date = "tomorrow" if "tomorrow" in text else "next monday"

        city_aliases = {
            "banglore": "Bangalore", "bangalore": "Bangalore", "bengaluru": "Bangalore", "blr": "Bangalore",
            "delhi": "Delhi", "new delhi": "Delhi", "del": "Delhi",
            "mumbai": "Mumbai", "bombay": "Mumbai", "bom": "Mumbai",
            "kerala": "Kerala", "amritsar": "Amritsar", "goa": "Goa",
            "chennai": "Chennai", "madras": "Chennai", "kolkata": "Kolkata",
            "hyderabad": "Hyderabad", "pune": "Pune", "jaipur": "Jaipur", "kochi": "Kochi"
        }

        if intent and intent.new_values:
            dest = intent.new_values.get("destination", dest)
            origin = intent.new_values.get("origin", origin)
            slot = intent.new_values.get("slot", slot)
            date = intent.new_values.get("date", date)
        
        # Direct text matching fallback for destination
        for key, val in city_aliases.items():
            if key in text:
                if "from " + key in text:
                    origin = val
                else:
                    dest = val
                break

        # Normalize dest and origin
        dest = city_aliases.get(dest.lower(), dest)
        origin = city_aliases.get(origin.lower(), origin)

        # Multi-turn Domain Detection & Memory
        has_hotel_kw = any(w in text for w in ["hotel", "stay", "room", "resort", "motel", "accommodation", "lodge"])
        has_cab_kw = any(w in text for w in ["cab", "taxi", "ride", "uber", "ola", "drive"])
        has_flight_kw = any(w in text for w in ["flight", "fly", "plane", "airline", "airport", "ticket", "fly to"])

        if has_hotel_kw:
            domain = "hotel"
        elif has_cab_kw:
            domain = "cab"
        elif has_flight_kw:
            domain = "flight"
        else:
            # Inherit active session domain (e.g. if user was looking for hotels and said "actually in banglore")
            domain = self.session_domains.get(session_id, "flight")

        self.session_domains[session_id] = domain

        is_hotel = (domain == "hotel")
        is_cab = (domain == "cab")
        is_flight = (domain == "flight")

        if is_hotel:
            primary_plan = [
                PlanStep(
                    step_id="p_1",
                    tool="search_hotels",
                    params={"city": dest},
                    risk=get_authoritative_risk("search_hotels"),
                    depends_on=[]
                )
            ]
            shadow_branches = [
                ShadowBranch(
                    branch_id="SHADOW_1",
                    base_version=curr_ver,
                    hypothesis=f"User will require {dest} airport transit",
                    steps=[
                        PlanStep(
                            step_id="sh_1",
                            tool="search_cabs",
                            params={"to": f"{dest} Hotel"},
                            risk=get_authoritative_risk("search_cabs"),
                            depends_on=[]
                        )
                    ]
                )
            ]
        elif is_cab:
            primary_plan = [
                PlanStep(
                    step_id="p_1",
                    tool="search_cabs",
                    params={"to": f"{dest} Airport"},
                    risk=get_authoritative_risk("search_cabs"),
                    depends_on=[]
                )
            ]
            shadow_branches = []
        elif is_flight:
            primary_plan = [
                PlanStep(
                    step_id="p_1",
                    tool="search_flights",
                    params={"from": origin, "to": dest, "slot": slot, "date": date},
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
        else:
            # Out-of-scope request: politely redirect to travel-domain capabilities
            primary_plan = [
                PlanStep(
                    step_id="p_1",
                    tool="travel_info",
                    params={"query": utterance},
                    risk=get_authoritative_risk("travel_info"),
                    depends_on=[]
                )
            ]
            shadow_branches = []

        return PlanGenerateResponse(
            session_id=session_id,
            event_id=event_id,
            base_version=curr_ver,
            primary_plan=primary_plan,
            shadow_branches=shadow_branches
        )

plan_generator = PlanDAGGenerator()
