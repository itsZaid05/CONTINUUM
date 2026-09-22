"""
CONTINUUM Mock Tool Sandbox
Async tool implementations with realistic delays and external operation IDs.
Delays: flights (1.5s), hotels (1.2s), booking (2.0s), cab (0.8s), calendar (0.5s).
"""
import asyncio
import uuid
import time
from typing import Dict, Any

class MockToolSandbox:
    def __init__(self, speedup: float = 1.0):
        self.speedup = speedup  # Set to > 1.0 for fast test suites if desired

    async def search_flights(self, params: Dict[str, Any]) -> Dict[str, Any]:
        dest = params.get("to", "Bangalore")
        slot = params.get("slot", "morning")
        delay = 1.5 / self.speedup
        await asyncio.sleep(delay)
        return {
            "flight_id": f"FL_{dest[:3].upper()}_702",
            "destination": dest,
            "slot": slot,
            "airline": "Air India",
            "departure": "08:15 AM",
            "price_inr": 5400,
            "status": "AVAILABLE"
        }

    async def search_hotels(self, params: Dict[str, Any]) -> Dict[str, Any]:
        city = params.get("city", params.get("to", "Bangalore"))
        delay = 1.2 / self.speedup
        await asyncio.sleep(delay)
        return {
            "hotel_id": f"HTL_{city[:3].upper()}_101",
            "name": f"Grand {city} Hotel",
            "price_per_night": 4200,
            "rating": 4.6,
            "status": "AVAILABLE"
        }

    async def search_cabs(self, params: Dict[str, Any]) -> Dict[str, Any]:
        to_dest = params.get("to", "Bangalore Airport")
        delay = 0.8 / self.speedup
        await asyncio.sleep(delay)
        return {
            "cab_id": f"CAB_{uuid.uuid4().hex[:6]}",
            "destination": to_dest,
            "estimated_fare": 650,
            "eta_mins": 7,
            "type": "Airport Sedan"
        }

    async def check_calendar(self, params: Dict[str, Any]) -> Dict[str, Any]:
        delay = 0.5 / self.speedup
        await asyncio.sleep(delay)
        return {
            "calendar_id": "cal_primary",
            "has_conflict": False,
            "available_slots": ["08:00-12:00", "14:00-18:00"]
        }

    async def hold_seat(self, params: Dict[str, Any]) -> Dict[str, Any]:
        flight_id = params.get("flight_id", "FL_BLR_702")
        delay = 0.6 / self.speedup
        await asyncio.sleep(delay)
        hold_id = f"HLD_{uuid.uuid4().hex[:8]}"
        return {
            "hold_id": hold_id,
            "flight_id": flight_id,
            "hold_expires_in_secs": 600,
            "status": "HELD"
        }

    async def modify_booking(self, params: Dict[str, Any]) -> Dict[str, Any]:
        delay = 1.0 / self.speedup
        await asyncio.sleep(delay)
        return {
            "external_operation_id": f"ext_mod_{uuid.uuid4().hex[:6]}",
            "status": "MODIFIED",
            "updated_params": params
        }

    async def confirm_booking(self, params: Dict[str, Any]) -> Dict[str, Any]:
        hold_id = params.get("hold_id", "HLD_DEFAULT")
        delay = 2.0 / self.speedup
        await asyncio.sleep(delay)
        ext_id = f"ext_tx_{uuid.uuid4().hex[:6]}"
        return {
            "external_operation_id": ext_id,
            "booking_ref": f"BK_{uuid.uuid4().hex[:6].upper()}",
            "hold_id": hold_id,
            "status": "COMMITTED",
            "amount_paid": 5400
        }

    def get_tool_callable(self, tool_name: str):
        mapping = {
            "search_flights": self.search_flights,
            "search_hotels": self.search_hotels,
            "search_cabs": self.search_cabs,
            "check_calendar": self.check_calendar,
            "hold_seat": self.hold_seat,
            "modify_booking": self.modify_booking,
            "confirm_booking": self.confirm_booking
        }
        return mapping.get(tool_name, self.search_flights)

mock_sandbox = MockToolSandbox()
mock_sandbox_fast = MockToolSandbox(speedup=50.0)
