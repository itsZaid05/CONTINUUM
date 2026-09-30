"""
CONTINUUM 5-Way Arbiter & Intent Volatility Scorer (IVS)
Categorizes: MODIFY | ADD_CONSTRAINT | RETRACT | NEW_GOAL | NOISE
Computes IVS (0.0 - 1.0) and generates targeted clarifications when needed.
"""
import re
import time
from typing import Dict, Any, List, Optional
from backend.app.models.schemas import (
    DeltaType, AuthorizationType, IntentClassifyResponse
)
from backend.app.core.version_manager import version_manager

# Hesitation / Volatility markers
VOLATILITY_MARKERS = [
    "maybe", "wait", "not sure", "or rather", "hold on", "might",
    "actually", "thinking", "could be", "probably", "i guess", "change of mind"
]

RETRACTION_MARKERS = [
    "don't book", "dont book", "cancel", "stop", "nevermind", "abort", "do not book",
    "don't hold", "dont hold", "do not hold", "actually don't", "actually dont", "release"
]

class IntentArbiter:
    def classify(
        self,
        session_id: str,
        utterance: str,
        event_id: Optional[str] = None
    ) -> IntentClassifyResponse:
        """
        Single-call intent classifier + IVS computation. Target latency: <250ms.
        """
        text = utterance.strip().lower()
        history = version_manager.get_session_history(session_id)
        
        # 1. Compute baseline IVS from linguistic cues
        hesitation_count = sum(1 for m in VOLATILITY_MARKERS if m in text)
        base_ivs = min(1.0, hesitation_count * 0.25)
        
        # Add history rapid revision penalty
        if len(history) >= 2:
            time_since_last = (time.time() * 1000) - history[-1].timestamp_ms
            if time_since_last < 5000:  # revised in < 5 seconds
                base_ivs = min(1.0, base_ivs + 0.20)
                
        # 2. 5-Way Triage Logic
        delta_type: DeltaType = "MODIFY"
        auth: AuthorizationType = "IMPLIED"
        confidence = 0.95
        affected_fields: List[str] = []
        new_values: Dict[str, Any] = {}
        preserved_constraints: List[str] = []
        clarification_needed = False
        clarification_prompt = None

        # 2. 5-Way Triage Logic
        words = set(re.findall(r'\b\w+\b', text))
        noise_words = {"thanks", "thank", "you", "for", "the", "help", "cool", "ok", "okay", "hello", "there", "hi", "awesome", "appreciate", "it", "sounds", "good", "got"}
        
        # Check Retraction
        retract_markers = [
            "don't book", "dont book", "do not book",
            "don't hold", "dont hold", "do not hold",
            "don't reserve", "dont reserve", "do not reserve",
            "don't confirm", "dont confirm", "do not confirm",
            "cancel", "stop", "nevermind", "never mind", "abort",
            "actually don't", "actually dont", "actualy don't", "actualy dont",
            "release hold", "release seat", "drop it", "leave it",
            "don't want", "dont want"
        ]
        if any(r in text for r in retract_markers) or (("don't" in text or "dont" in text or "do not" in text) and any(w in text for w in ["hold", "book", "confirm", "reserve", "pay", "proceed", "buy", "ticket"])):
            delta_type = "RETRACT"
            ivs_score = max(0.15, base_ivs)
            auth = "NONE"
            affected_fields = ["booking", "payment", "hold_seat"]
        
        # Check Noise / Chit-chat
        elif words.issubset(noise_words) or text in ["sounds good", "got it", "okay sounds good", "awesome appreciate it", "thanks a lot for the help", "hello there"]:
            delta_type = "NOISE"
            ivs_score = 0.05
            auth = "NONE"
            confidence = 0.99

        # Check Add Constraint
        elif any(k in text for k in [
            "only ", "direct flight", "window seat", "budget under",
            "make sure", "must arrive", "must include", "preference for", "add constraint"
        ]):
            delta_type = "ADD_CONSTRAINT"
            ivs_score = max(0.20, base_ivs)
            auth = "IMPLIED"
            if "direct" in text:
                new_values["flight_type"] = "direct"
                affected_fields = ["flight_type"]
            elif "window" in text:
                new_values["seat_pref"] = "window"
                affected_fields = ["seat_pref"]

        # Check New Goal (New domain / task)
        elif any(k in text for k in [
            "book a hotel", "check weather", "switch to cab", "find nearby",
            "look up", "train schedules", "flight status", "switch to "
        ]) and not any(m in text for m in ["instead of delhi", "switch the date", "switch the time"]):
            delta_type = "NEW_GOAL"
            ivs_score = max(0.20, base_ivs)
            auth = "IMPLIED"
            affected_fields = ["goal"]

        # Check Modify (Default for parameter pivots)
        else:
            delta_type = "MODIFY"
            ivs_score = max(0.30, base_ivs)
            
            # Dynamic destination and origin extraction
            dest_match = re.search(r'\b(?:to|change it to|make it to|make it|switch to)\s+([a-zA-Z\s]+?)(?=\s+(?:for|tomorrow|today|next|morning|evening|afternoon|night|but|\,)|$)', text)
            from_match = re.search(r'\bfrom\s+([a-zA-Z\s]+?)(?=\s+(?:to|for|tomorrow|today|\,)|$)', text)
            
            if dest_match:
                extracted_dest = dest_match.group(1).strip().title()
                # Clean up common filler words
                extracted_dest = re.sub(r'^(?:the|a|an)\s+', '', extracted_dest, flags=re.IGNORECASE)
                if extracted_dest and len(extracted_dest) > 1:
                    affected_fields.append("destination")
                    new_values["destination"] = extracted_dest
            
            if from_match:
                extracted_from = from_match.group(1).strip().title()
                if extracted_from and len(extracted_from) > 1:
                    affected_fields.append("origin")
                    new_values["origin"] = extracted_from

            # Known city dictionary fallback & aliases
            known_cities = {
                "amritsar": "Amritsar", "kerala": "Kerala", "new delhi": "New Delhi", "delhi": "Delhi",
                "bangalore": "Bangalore", "banglore": "Bangalore", "bengaluru": "Bangalore", "blr": "Bangalore",
                "mumbai": "Mumbai", "bombay": "Mumbai", "bom": "Mumbai",
                "goa": "Goa", "chennai": "Chennai", "madras": "Chennai", "maa": "Chennai",
                "kolkata": "Kolkata", "calcutta": "Kolkata", "ccu": "Kolkata",
                "hyderabad": "Hyderabad", "hyd": "Hyderabad",
                "pune": "Pune", "jaipur": "Jaipur", "srinagar": "Srinagar", "kochi": "Kochi", "cochin": "Kochi",
                "ahmedabad": "Ahmedabad", "chandigarh": "Chandigarh", "varanasi": "Varanasi", "lucknow": "Lucknow",
                "london": "London", "paris": "Paris", "dubai": "Dubai", "singapore": "Singapore", "new york": "New York"
            }
            for key, val in known_cities.items():
                if key in text:
                    if "from " + key in text or "departing " + key in text:
                        new_values["origin"] = val
                        if "origin" not in affected_fields: affected_fields.append("origin")
                    else:
                        new_values["destination"] = val
                        if "destination" not in affected_fields: affected_fields.append("destination")
                    break

            # Check preserved constraints & time slot
            if "morning" in text:
                preserved_constraints.append("departure_time: morning")
                new_values["slot"] = "morning"
            elif "evening" in text:
                preserved_constraints.append("departure_time: evening")
                new_values["slot"] = "evening"
            
            if "tomorrow" in text:
                new_values["date"] = "tomorrow"
            elif "next monday" in text:
                new_values["date"] = "next monday"

            if "direct" in text:
                preserved_constraints.append("flight_type: direct")

            # Check explicit auth
            if "confirm" in text or "yes book it" in text or "proceed to pay" in text:
                auth = "EXPLICIT"

        # Check Ambiguity & Clarification triggers
        if ivs_score > 0.60 and delta_type in ["MODIFY", "ADD_CONSTRAINT"]:
            clarification_needed = True
            clarification_prompt = "You seemed unsure about your destination or flight timing. Would you like me to hold or proceed with searching?"

        curr_ver = version_manager.get_current_version(session_id)
        return IntentClassifyResponse(
            event_id=event_id or f"evt_v{curr_ver}",
            session_id=session_id,
            utterance=utterance,
            delta_type=delta_type,
            confidence=confidence,
            ivs_score=round(ivs_score, 2),
            authorization=auth,
            affected_fields=affected_fields,
            new_values=new_values,
            preserved_constraints=preserved_constraints,
            clarification_needed=clarification_needed,
            clarification_prompt=clarification_prompt
        )

intent_arbiter = IntentArbiter()
