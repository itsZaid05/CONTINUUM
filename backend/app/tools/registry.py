"""
CONTINUUM Authoritative Tool Registry
Single source of truth for tool risk tiers across multi-domain workflows.
Backend registry strictly overrides planner risk.
"""

from backend.app.models.schemas import RiskTier

# Strict Tool Risk Tier Registry across all 5 Hackathon Domains
TOOL_REGISTRY: dict[str, RiskTier] = {
    # Travel & Transit
    "search_flights": "FREE",
    "search_hotels": "FREE",
    "search_cabs": "FREE",
    "check_calendar": "FREE",
    "travel_info": "FREE",
    "get_user_preferences": "FREE",
    "hold_seat": "STAGEABLE",
    "reserve_hotel": "STAGEABLE",
    "draft_itinerary": "STAGEABLE",
    "modify_booking": "MUTATING",
    "cancel_booking": "MUTATING",
    "confirm_booking": "IRREVERSIBLE",
    "process_payment": "IRREVERSIBLE",
    # In-Car & Navigation
    "navigate_route": "FREE",
    "set_climate": "MUTATING",
    # Dining & Food
    "reserve_table": "STAGEABLE",
    # Device Diagnostics & Troubleshooting
    "diagnose_device": "FREE",
}


def get_authoritative_risk(tool_name: str) -> RiskTier:
    """
    Returns the authoritative risk tier for a tool. Defaults to IRREVERSIBLE if unknown for safety.
    """
    return TOOL_REGISTRY.get(tool_name, "IRREVERSIBLE")
