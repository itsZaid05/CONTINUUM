"""
CONTINUUM Authoritative Tool Registry
Single source of truth for tool risk tiers. Backend registry strictly overrides planner risk.
Theme 5 scope: strictly travel/booking domain (flights, hotels, cabs, booking).
"""
from typing import Dict
from backend.app.models.schemas import RiskTier

# Strict Tool Risk Tier Registry
TOOL_REGISTRY: Dict[str, RiskTier] = {
    "search_flights":  "FREE",
    "search_hotels":   "FREE",
    "search_cabs":     "FREE",
    "check_calendar":  "FREE",
    "travel_info":     "FREE",
    "get_user_preferences": "FREE",
    "hold_seat":       "STAGEABLE",
    "draft_itinerary": "STAGEABLE",
    "modify_booking":  "MUTATING",
    "cancel_booking":  "MUTATING",
    "confirm_booking": "IRREVERSIBLE",
    "process_payment": "IRREVERSIBLE",
}

def get_authoritative_risk(tool_name: str) -> RiskTier:
    """
    Returns the authoritative risk tier for a tool. Defaults to IRREVERSIBLE if unknown for safety.
    """
    return TOOL_REGISTRY.get(tool_name, "IRREVERSIBLE")
