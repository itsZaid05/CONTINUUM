"""
CONTINUUM Mock Tool Sandbox
Async tool implementations with realistic delays so interruptions can be demonstrated.
Delays: flights (5.0s), hotels (4.0s), cabs (3.0s), booking (2.0s), calendar (0.5s).
All tools are strictly travel-domain: flights, hotels, cabs, booking. (Theme 5 scope)
"""
import asyncio
import uuid
from typing import Dict, Any


class MockToolSandbox:
    def __init__(self, speedup: float = 1.0):
        self.speedup = speedup  # Set > 1.0 for fast test suites

    async def search_flights(self, params: Dict[str, Any]) -> Dict[str, Any]:
        origin = params.get("from", "Delhi")
        dest = params.get("to", "Bangalore")
        slot = params.get("slot", "morning")
        date = params.get("date", "tomorrow")
        orig_code = origin[:3].upper() if len(origin) >= 3 else "DEL"
        dest_code = dest[:3].upper() if len(dest) >= 3 else "BLR"
        base_price = 4500 + (abs(hash(dest)) % 1500)

        # 7-second realistic delay — gives the user ample time to type an interruption live
        await asyncio.sleep(7.0 / self.speedup)

        flights = [
            {
                "flight_id": f"6E_{orig_code}{dest_code}_401",
                "airline": "IndiGo",
                "flight_number": f"6E-{abs(hash(dest)) % 700 + 100}",
                "origin": origin,
                "destination": dest,
                "departure": "07:15 AM",
                "arrival": "09:50 AM",
                "duration": "2h 35m (Non-stop)",
                "price_inr": base_price,
                "slot": "morning",
                "status": "AVAILABLE"
            },
            {
                "flight_id": f"AI_{orig_code}{dest_code}_502",
                "airline": "Air India",
                "flight_number": f"AI-{abs(hash(dest)) % 600 + 200}",
                "origin": origin,
                "destination": dest,
                "departure": "01:30 PM",
                "arrival": "04:10 PM",
                "duration": "2h 40m (Non-stop)",
                "price_inr": base_price + 650,
                "slot": "afternoon",
                "status": "AVAILABLE"
            },
            {
                "flight_id": f"UK_{orig_code}{dest_code}_819",
                "airline": "Vistara",
                "flight_number": f"UK-{abs(hash(dest)) % 500 + 400}",
                "origin": origin,
                "destination": dest,
                "departure": "06:45 PM",
                "arrival": "09:20 PM",
                "duration": "2h 35m (Non-stop)",
                "price_inr": base_price + 1200,
                "slot": "evening",
                "status": "AVAILABLE"
            }
        ]

        return {
            "flight_id": flights[0]["flight_id"],
            "origin": origin,
            "destination": dest,
            "slot": slot,
            "date": date,
            "airline": flights[0]["airline"],
            "flight_number": flights[0]["flight_number"],
            "departure": flights[0]["departure"],
            "arrival": flights[0]["arrival"],
            "duration": flights[0]["duration"],
            "price_inr": flights[0]["price_inr"],
            "flights": flights,
            "status": "AVAILABLE"
        }

    async def search_hotels(self, params: Dict[str, Any]) -> Dict[str, Any]:
        city = params.get("city", params.get("to", "Delhi"))
        # 7-second realistic delay
        await asyncio.sleep(7.0 / self.speedup)
        city_code = city[:3].upper() if len(city) >= 3 else "DEL"
        base_rate = 3800 + (abs(hash(city)) % 1500)

        hotels = [
            {
                "hotel_id": f"HTL_{city_code}_101",
                "name": f"The Grand {city} Palace",
                "price_per_night": base_rate + 1200,
                "rating": 4.8,
                "amenities": ["Free WiFi", "Breakfast Included", "Swimming Pool", "Spa"],
                "city": city,
                "status": "AVAILABLE"
            },
            {
                "hotel_id": f"HTL_{city_code}_204",
                "name": f"Radisson Blu {city} Airport",
                "price_per_night": base_rate,
                "rating": 4.5,
                "amenities": ["Free WiFi", "Airport Shuttle", "Fitness Center"],
                "city": city,
                "status": "AVAILABLE"
            },
            {
                "hotel_id": f"HTL_{city_code}_308",
                "name": f"Ginger Hotel & Suites {city}",
                "price_per_night": max(2200, base_rate - 1100),
                "rating": 4.2,
                "amenities": ["Free WiFi", "24/7 Room Service"],
                "city": city,
                "status": "AVAILABLE"
            }
        ]

        return {
            "city": city,
            "hotel_id": hotels[0]["hotel_id"],
            "name": hotels[0]["name"],
            "price_per_night": hotels[0]["price_per_night"],
            "rating": hotels[0]["rating"],
            "hotels": hotels,
            "status": "AVAILABLE"
        }

    async def search_cabs(self, params: Dict[str, Any]) -> Dict[str, Any]:
        to_dest = params.get("to", "Airport")
        # 5-second realistic delay
        await asyncio.sleep(5.0 / self.speedup)
        return {
            "cab_id": f"CAB_{uuid.uuid4().hex[:6]}",
            "destination": to_dest,
            "estimated_fare": 550,
            "eta_mins": 4,
            "type": "Airport Sedan (Uber Premier)",
            "driver_name": "Rajesh Kumar",
            "rating": 4.9,
            "status": "AVAILABLE"
        }

    async def travel_info(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Travel-domain informational fallback.
        Handles questions about routes, travel tips, luggage, and visa — staying strictly
        in-scope for Theme 5 (travel/booking domain). Does NOT answer weather, education,
        or general knowledge questions.
        """
        query = params.get("query", "")
        await asyncio.sleep(0.5 / self.speedup)
        return {
            "query": query,
            "answer": (
                "I'm CONTINUUM — your real-time interruptible travel assistant. "
                "I can help you search flights, hotels, and cabs, hold seats, and confirm bookings. "
                "Try: 'Find morning flights from Delhi to Bangalore' or 'Book a hotel in Mumbai'."
            ),
            "status": "COMPLETED"
        }

    async def check_calendar(self, params: Dict[str, Any]) -> Dict[str, Any]:
        await asyncio.sleep(0.5 / self.speedup)
        return {
            "calendar_id": "cal_primary",
            "has_conflict": False,
            "available_slots": ["08:00-12:00", "14:00-18:00"]
        }

    async def hold_seat(self, params: Dict[str, Any]) -> Dict[str, Any]:
        flight_id = params.get("flight_id", "FL_BLR_702")
        await asyncio.sleep(0.6 / self.speedup)
        hold_id = f"HLD_{uuid.uuid4().hex[:8]}"
        return {
            "hold_id": hold_id,
            "flight_id": flight_id,
            "hold_expires_in_secs": 600,
            "status": "HELD"
        }

    async def modify_booking(self, params: Dict[str, Any]) -> Dict[str, Any]:
        await asyncio.sleep(1.0 / self.speedup)
        return {
            "external_operation_id": f"ext_mod_{uuid.uuid4().hex[:6]}",
            "status": "MODIFIED",
            "updated_params": params
        }

    async def confirm_booking(self, params: Dict[str, Any]) -> Dict[str, Any]:
        hold_id = params.get("hold_id", "HLD_DEFAULT")
        await asyncio.sleep(2.0 / self.speedup)
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
            "confirm_booking": self.confirm_booking,
            "travel_info": self.travel_info,
        }
        return mapping.get(tool_name, self.travel_info)


mock_sandbox = MockToolSandbox()
mock_sandbox_fast = MockToolSandbox(speedup=50.0)
