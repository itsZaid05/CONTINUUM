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
        # Multi-turn session domain and location tracking (flight, hotel, cab, origin, destination)
        self.session_domains: Dict[str, str] = {}
        self.session_origins: Dict[str, str] = {}
        self.session_dests: Dict[str, str] = {}

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

        # Retain multi-turn origin and destination memory from session
        dest = self.session_dests.get(session_id, "Bangalore")
        origin = self.session_origins.get(session_id, "Delhi")
        slot = "morning" if "morning" in text else "anytime"
        date = "tomorrow" if "tomorrow" in text else "next monday"

        import re
        city_aliases = {
            "banglore": "Bangalore", "bangalore": "Bangalore", "bengaluru": "Bangalore", "blr": "Bangalore",
            "delhi": "Delhi", "new delhi": "Delhi", "del": "Delhi",
            "mumbai": "Mumbai", "bombay": "Mumbai", "bom": "Mumbai",
            "chennai": "Chennai", "madras": "Chennai", "kolkata": "Kolkata",
            "hyderabad": "Hyderabad", "pune": "Pune", "jaipur": "Jaipur", "kochi": "Kochi",
            "amritsar": "Amritsar", "jammu": "Jammu", "chandigarh": "Chandigarh", "goa": "Goa"
        }

        # 1. Intent values if provided
        if intent and intent.new_values:
            dest = intent.new_values.get("destination", dest)
            origin = intent.new_values.get("origin", origin)
            slot = intent.new_values.get("slot", slot)
            date = intent.new_values.get("date", date)

        # 2. Generalized Dynamic Entity Extraction for any arbitrary locations worldwide
        from_m = re.search(r'\bfrom\s+([a-zA-Z0-9\s&\'\-]+?)(?:\s+to\b|\s+for\b|\s+tomorrow|\s+on\b|\s+next|,|$)', text, re.IGNORECASE)
        to_m = re.search(r'\b(?:check|how about|what about|look for|try|switch to|fly to|search|towards|into|in|to|make it|change to)\s+([a-zA-Z0-9\s&\'\-]+?)(?:\s+too|\s+as well|\s+instead|\s+also|\s+for\b|\s+tomorrow|\s+on\b|\s+next|\s+avoid|\s+via|\s+using|\s+with|,|$)', text, re.IGNORECASE)
        
        if from_m:
            cand_from = re.sub(r'^(?:the|a|an)\s+', '', from_m.group(1).strip(), flags=re.IGNORECASE).title()
            if cand_from:
                origin = cand_from
        
        if to_m:
            cand_to = re.sub(r'^(?:the|a|an)\s+', '', to_m.group(1).strip(), flags=re.IGNORECASE).title()
            if cand_to and cand_to.lower() not in ["hotel", "flight", "cab", "table", "seat", "room", "delhi", "bangalore"]:
                dest = cand_to
            elif cand_to:
                dest = cand_to

        # Normalize known aliases if matched
        dest = city_aliases.get(dest.lower(), dest)
        origin = city_aliases.get(origin.lower(), origin)

        # Save active memory
        self.session_origins[session_id] = origin
        self.session_dests[session_id] = dest

        # Multi-turn Domain Detection & Memory
        # Action triggers take strict priority over question/info starters
        is_direct_action = any(w in text for w in [
            "reserve a table", "reserve table", "book table", "book a table", "table for", "party of",
            "reserve room", "book room", "confirm room", "reserve a room", "book a room", "search hotel",
            "search flight", "book flight", "fly to", "search cabs", "book cab", "navigate", "reroute",
            "set ac", "temp to", "cool cabin", "diagnose", "e-401",
            # Conversational flight-action phrases that start with "can you / find / get me"
            "find flight", "find a flight", "get me a flight", "look for a flight",
            "find hotel", "find a hotel", "get me a hotel", "look for a hotel",
            "find cab", "find a cab", "book me a flight", "book me a hotel"
        ])

        question_starters = [
            "who", "what", "where", "when", "why", "how", "which", "tell me", "explain", "is it",
            "recommend", "suggest", "popular", "best", "famous", "top item", "item to try", "items to try",
            "dishes", "menu", "specialty", "places to visit", "things to do", "attractions", "sightseeing",
            "help", "features", "what to eat", "food item", "prime minister", "president", "capital", "weather"
        ]
        # "can you" is only an info query if no action domain keyword is also present
        has_question_or_info = (
            any(text.startswith(w) or f" {w} " in f" {text} " for w in question_starters)
            or text.endswith("?")
        )

        has_flight_kw = any(w in text for w in ["flight", "fly", "plane", "airline", "fly to", "air ticket", "book flight", "flights"])
        has_dining_kw = any(w in text for w in ["dining", "restaurant", "table for", "reserve a table", "dinner table", "lunch table", "reservation", "reserve table", "book table", "book a table", "book a dining table", "dhaba", "bistro", "cafe"])
        has_hotel_kw = (any(w in text for w in ["hotel room", "stay in", "book hotel", "search hotel", "resort", "motel", "accommodation", "lodge", "reserve room", "book room", "hotel", "room", "stay"]) and not has_dining_kw)
        has_cab_kw = any(w in text for w in ["cab", "taxi", "ride", "uber", "ola"])
        has_nav_kw = any(w in text for w in ["navigate", "navigation", "route", "direction", "drive to", "reroute", "avoid toll", "avoid highway", "ring road"])
        has_climate_kw = any(w in text for w in ["temperature", "set ac", "temp to", "set climate", "cool cabin"])
        has_diag_kw = any(w in text for w in ["diagnos", "error code", "troubleshoot", "manual", "device error", "e-401", "e-", "camera frame"])

        # Action domains take priority — info/Gemini only wins when NO action domain matched
        if has_flight_kw: domain = "flight"
        elif has_dining_kw: domain = "dining"
        elif has_hotel_kw: domain = "hotel"
        elif has_nav_kw: domain = "navigation"
        elif has_climate_kw: domain = "climate"
        elif has_diag_kw: domain = "diagnostics"
        elif has_cab_kw: domain = "cab"
        elif has_question_or_info and not is_direct_action: domain = "info"
        else:
            domain = self.session_domains.get(session_id, "flight")

        self.session_domains[session_id] = domain


        if domain == "info":
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
        elif domain == "hotel":
            is_reserve = any(w in text for w in ["reserve room", "book room", "confirm room", "reserve a room", "book a room"])
            if is_reserve:
                hotel_match = re.search(r'\b(?:at|for|in)\s+([a-zA-Z0-9\s&\'\-]+?)(?:\s+(?:tonight|today|tomorrow|for\s+\d+|\,|$))', text, re.IGNORECASE)
                hotel_target = hotel_match.group(1).strip().title() if hotel_match else f"The Grand {dest} Palace"
                primary_plan = [
                    PlanStep(
                        step_id="p_1",
                        tool="reserve_hotel",
                        params={"hotel_name": hotel_target, "city": dest, "nights": 1, "price_per_night": 4500},
                        risk=get_authoritative_risk("reserve_hotel"),
                        depends_on=[]
                    )
                ]
                shadow_branches = []
            else:
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
        elif domain == "navigation":
            import re
            avoid_target = "highways" if "highway" in text else ("tolls" if "toll" in text else "traffic")
            nav_dest = dest
            nav_dest_match = re.search(r'\b(?:to|towards|route to|navigate to)\s+([a-zA-Z0-9\s&\'\-]+?)(?:\s*,\s*|\s+(?:avoid|avoiding|via|using|with|instead)|$)', text, re.IGNORECASE)
            if nav_dest_match:
                candidate = nav_dest_match.group(1).strip()
                if candidate.lower().endswith(" instead"):
                    candidate = candidate[:-8].strip()
                if candidate and candidate.lower() not in ["the", "a", "an"]:
                    nav_dest = candidate.title()
            
            via_match = re.search(r'\b(?:via|using)\s+([a-zA-Z0-9\s&\'\-]+?)(?:\s*,\s*|\s+(?:avoid|avoiding|instead)|$)', text, re.IGNORECASE)
            via_val = via_match.group(1).strip().title() if via_match else None

            nav_params = {"destination": nav_dest, "avoid": avoid_target}
            if via_val:
                nav_params["via"] = via_val

            primary_plan = [
                PlanStep(
                    step_id="p_1",
                    tool="navigate_route",
                    params=nav_params,
                    risk=get_authoritative_risk("navigate_route"),
                    depends_on=[]
                )
            ]
            shadow_branches = []
        elif domain == "climate":
            import re
            temp_match = re.search(r'(\d{2})', text)
            temp_val = int(temp_match.group(1)) if temp_match else 22
            primary_plan = [
                PlanStep(
                    step_id="p_1",
                    tool="set_climate",
                    params={"temperature": temp_val},
                    risk=get_authoritative_risk("set_climate"),
                    depends_on=[]
                )
            ]
            shadow_branches = []
        elif domain == "dining":
            import re
            is_dining_reserve = any(w in text for w in ["reserve a table", "dinner table", "lunch table", "reservation", "reserve table", "book table", "book a table", "book a dining table", "table for", "party of"])
            is_info_query = any(w in text for w in ["what is", "best food", "recommend", "dishes", "menu", "specialty", "famous", "top item", "what to eat", "review", "rating", "places to eat", "good food"])

            if is_info_query and not is_dining_reserve:
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
            else:
                time_match = re.search(r'\b(?:at|for)?\s*(\d{1,2}(?::\d{2})?\s*(?:pm|am))\b', text, re.IGNORECASE)
                time_val = "08:30 PM"
                if time_match:
                    raw_time = time_match.group(1).upper().strip()
                    if ":" not in raw_time:
                        num_part = re.search(r'\d+', raw_time).group(0)
                        meridiem = "PM" if "PM" in raw_time else "AM"
                        time_val = f"{int(num_part):02d}:00 {meridiem}"
                    else:
                        time_val = raw_time

                guests_match = re.search(r'\b(?:table for|for|party of)\s+(\d+)(?!\s*(?:pm|am|:|o\'clock))\b', text, re.IGNORECASE)
                guests_val = int(guests_match.group(1)) if guests_match else 2

                rest_match = re.search(r'\bat\s+([a-zA-Z0-9\s&\'\-]+?)(?:\s+(?:tonight|today|tomorrow|this\s+evening|for\s+\d+|\d+\s*pm|\d+\s*am|\d+:\d+|\,|$))', text, re.IGNORECASE)
                if rest_match:
                    restaurant_name = rest_match.group(1).strip().title()
                else:
                    restaurant_name = f"The Grand {dest} Bistro"

                primary_plan = [
                    PlanStep(
                        step_id="p_1",
                        tool="reserve_table",
                        params={"restaurant": restaurant_name, "guests": guests_val, "time": time_val},
                        risk=get_authoritative_risk("reserve_table"),
                        depends_on=[]
                    )
                ]
                shadow_branches = []
        elif domain == "diagnostics":
            import re
            err_match = re.search(r'\b(e-?\d{3,4})\b', text, re.IGNORECASE)
            err_code = err_match.group(1).upper() if err_match else "E-401"
            primary_plan = [
                PlanStep(
                    step_id="p_1",
                    tool="diagnose_device",
                    params={"error_code": err_code, "device_model": "Galaxy Device / Smart Hub"},
                    risk=get_authoritative_risk("diagnose_device"),
                    depends_on=[]
                )
            ]
            shadow_branches = []
        elif domain == "cab":
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
        elif domain == "flight":
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
