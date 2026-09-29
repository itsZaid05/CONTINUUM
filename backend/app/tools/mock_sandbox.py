import os
import asyncio
import uuid
from typing import Dict, Any, Optional
import httpx


async def call_gemini_qa(prompt: str) -> Optional[str]:
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
    if not key:
        return None
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    system_instruction = (
        "You are CONTINUUM, an intelligent real-time travel, dining, lifestyle, and diagnostics assistant. "
        "Provide a concise, helpful, and beautifully formatted response with bullet points if applicable. "
        "If the user asks about a restaurant, dish, or place, give genuine recommendations and mention they can ask you to reserve a table."
    )
    payload = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 400}
    }
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                cands = data.get("candidates", [])
                if cands:
                    parts = cands[0].get("content", {}).get("parts", [])
                    text = "".join(p.get("text", "") for p in parts).strip()
                    if text:
                        return text
    except Exception:
        pass
    return None


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

        # 5.0-second delay — provides comfortable window for live interruption demonstration
        await asyncio.sleep(5.0 / self.speedup)

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
        # 5.0-second delay
        await asyncio.sleep(5.0 / self.speedup)
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
        # 4.0-second delay
        await asyncio.sleep(4.0 / self.speedup)
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
        query = params.get("query", "")

        # 1. If Gemini API Key is provided, call Gemini 2.5 Flash for true open-domain reasoning
        if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
            gemini_ans = await call_gemini_qa(query)
            if gemini_ans:
                return {"query": query, "answer": gemini_ans, "status": "COMPLETED"}

        await asyncio.sleep(0.5 / self.speedup)
        
        import re
        q_clean = query.strip()
        q_lower = q_clean.lower()
        
        # 1. System / Capability Queries
        if any(w in q_lower for w in ["what can you do", "features", "who are you", "what is continuum", "how do you work", "help"]):
            answer = (
                "⚡ **CONTINUUM Real-Time Orchestrator Capabilities:**\n"
                "• ✈️ **Flight Management:** Multi-carrier search, seat hold, and instant confirmation.\n"
                "• 🏨 **Hotel Reservations:** Instant booking with price calculation across top cities.\n"
                "• 🚕 **Ground Transit:** Speculative cab search & airport transfers.\n"
                "• 🗺️ **In-Car Navigation & Climate:** Real-time rerouting, toll avoidance, and cabin climate controls.\n"
                "• 🍽️ **Dining:** Table reservations & culinary recommendations.\n"
                "• 🔧 **Device Diagnostics & OCR:** Real-time error code recovery & multimodal ticket parsing.\n\n"
                "⏱️ *Features sub-millisecond interrupts (<1ms Fast ACK) and zero-leak DAG rollbacks!*"
            )
            return {"query": query, "answer": answer, "status": "COMPLETED"}

        # Known cities list for geo-tagging
        cities = ["amritsar", "delhi", "mumbai", "bangalore", "bengaluru", "chennai", "kolkata", "hyderabad", "pune", "jaipur", "kochi", "goa", "chandigarh", "agra", "lucknow", "varanasi", "ahmedabad"]
        detected_city = None
        for c in cities:
            if re.search(rf'\b{c}\b', q_lower):
                detected_city = c.title()
                break

        # 2. Food & Restaurant Recommendations (Generalized for ANY restaurant/dish worldwide)
        food_indicators = ["best food", "best item", "item to try", "items to try", "what to eat", "recommend", "dishes", "menu", "specialty", "food item", "famous food", "top dish", "cuisine", "taste", "good food", "food"]
        is_food_query = any(ind in q_lower for ind in food_indicators) or any(w in q_lower for w in ["restaurant", "dhaba", "bistro", "cafe", "diner", "eatery"])

        if is_food_query:
            # Extract venue name dynamically
            venue = None
            venue_match = re.search(r'\b(?:at|in|from|for|of)\s+([a-zA-Z0-9\s&\'\-]+?)(?:\s+(?:restaurant|dhaba|bistro|cafe|lounge|bar|tonight|today|tomorrow|\,|$))', q_clean, re.IGNORECASE)
            if venue_match:
                cand = venue_match.group(1).strip()
                for c in cities:
                    if cand.lower().endswith(f" {c}"):
                        cand = cand[:-len(c)-1].strip()
                        if not detected_city:
                            detected_city = c.title()
                if cand and cand.lower() not in ["the", "a", "an", "this", "that", "any", "good", "best"]:
                    venue = cand.title()

            if not venue:
                for w in ["restaurant", "dhaba", "bistro", "cafe"]:
                    m = re.search(rf'([a-zA-Z0-9\s&\'\-]+?)\s+{w}', q_clean, re.IGNORECASE)
                    if m:
                        cand = m.group(1).strip()
                        cand = re.sub(r'^(?:what|which|is|the|best|food|item|to|try|at|in)\s+', '', cand, flags=re.IGNORECASE).strip()
                        if cand:
                            venue = cand.title()
                            break

            if venue:
                city_str = f" ({detected_city})" if detected_city else ""
                answer = (
                    f"🍽️ **{venue}{city_str} — Top Recommended Items:**\n"
                    f"• **Chef's House Specialty:** Signature slow-braised specialty platter prepared with authentic regional spices.\n"
                    f"• **Popular Small Plates:** Crisp artisanal tandoori / grilled appetizers and house-made dips.\n"
                    f"• **Must-Try Mains:** Rich gourmet curry / signature main served with freshly baked artisan breads.\n"
                    f"• **Signature Dessert / Drink:** Traditional house dessert & handcrafted beverage.\n\n"
                    f"💡 *Say 'Book table for 2 at {venue} tonight' if you'd like me to reserve a table.*"
                )
            elif detected_city:
                answer = (
                    f"🍛 **Top Culinary Highlights in {detected_city}:**\n"
                    f"• **Local Signature Dishes:** Renowned regional thali, iconic street delicacies, and authentic slow-cooked specials.\n"
                    f"• **Famous Eateries:** Top heritage food hubs, iconic dhabas, and celebrated dining bistros.\n"
                    f"• **Desserts:** Traditional regional sweets and signature coolers.\n\n"
                    f"💡 *Name any restaurant (e.g., 'What is best at [Restaurant] in {detected_city}?') or ask me to book a table!*"
                )
            else:
                answer = (
                    "🍽️ **Top Culinary Recommendations:**\n"
                    "• **Signature Specialties:** Authentic local thali, tandoori platters, artisanal curries, and fresh breads.\n"
                    "• **Desserts:** Traditional regional sweets, kulfi, and house specialties.\n\n"
                    "💡 *Say 'Book a table for 2 tonight' when you're ready to make a reservation.*"
                )
            return {"query": query, "answer": answer, "status": "COMPLETED"}

        # 3. Sightseeing / Places / Travel Inquiry (Generalized for ANY city)
        if any(w in q_lower for w in ["places to visit", "sightseeing", "attractions", "things to do", "explore", "visit", "places"]):
            city_name = detected_city or "your destination"
            answer = (
                f"🗺️ **Top Attractions & Sights in {city_name}:**\n"
                f"• **Historic Landmarks & Heritage Sites:** Iconic monuments and architectural highlights.\n"
                f"• **Cultural & Vibrant Hubs:** Local markets, cultural promenades, and scenic viewpoints.\n"
                f"• **Transit Advice:** Accessible via local cabs or direct in-car navigation.\n\n"
                f"💡 *Say 'Navigate to [Attraction]' or 'Find hotels in {city_name}' to plan your trip.*"
            )
            return {"query": query, "answer": answer, "status": "COMPLETED"}

        # 4. General Contextual Response
        answer = (
            f"ℹ️ **CONTINUUM Travel & Lifestyle Assistant**\n\n"
            f"Regarding: *\"{query}\"*\n"
            f"I'm continuously monitoring your trip and lifestyle parameters. You can ask for recommendations, search flights, book hotels, reserve dining tables, or get in-car navigation in real time."
        )
        return {"query": query, "answer": answer, "status": "COMPLETED"}

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

    async def navigate_route(self, params: Dict[str, Any]) -> Dict[str, Any]:
        dest = params.get("destination", params.get("to", "City Center"))
        avoid = params.get("avoid", "tolls")
        via = params.get("via", f"Expressway / Outer Ring Road (avoiding {avoid})")
        await asyncio.sleep(5.0 / self.speedup)
        return {
            "route_id": f"RT_{uuid.uuid4().hex[:6]}",
            "destination": dest,
            "eta_mins": 28,
            "distance_km": 18.5,
            "via": via,
            "traffic": "Moderate (Fastest Route Selected)",
            "status": "NAVIGATING"
        }

    async def set_climate(self, params: Dict[str, Any]) -> Dict[str, Any]:
        temp = params.get("temperature", 22)
        await asyncio.sleep(0.8 / self.speedup)
        return {
            "target_temp_c": temp,
            "fan_speed": "Auto",
            "zone": "Cabin / Living Room",
            "status": "SET_SUCCESSFULLY"
        }

    async def reserve_table(self, params: Dict[str, Any]) -> Dict[str, Any]:
        restaurant = params.get("restaurant", "Mainland China")
        guests = params.get("guests", 4)
        time_slot = params.get("time", "08:30 PM")
        await asyncio.sleep(5.0 / self.speedup)
        return {
            "reservation_id": f"RES_{uuid.uuid4().hex[:6].upper()}",
            "restaurant": restaurant,
            "guests": guests,
            "time": time_slot,
            "table_type": "Indoor Booth",
            "status": "CONFIRMED"
        }

    async def diagnose_device(self, params: Dict[str, Any]) -> Dict[str, Any]:
        err = params.get("error_code", "E-401")
        model = params.get("device_model", "Galaxy S24 / SmartHub")
        await asyncio.sleep(4.0 / self.speedup)
        return {
            "error_code": err,
            "device": model,
            "diagnosis": "Network Handshake Timeout / Sensor Calibration Error",
            "remedy": "1. Power cycle device for 10s. 2. Reset WiFi cache via Settings > General > Reset.",
            "manual_ref": f"Manual Section 4.2 ({err} Recovery Protocol)",
            "status": "DIAGNOSED"
        }

    async def reserve_hotel(self, params: Dict[str, Any]) -> Dict[str, Any]:
        hotel_name = params.get("hotel_name", "Grand Palace Hotel")
        city = params.get("city", "Amritsar")
        nights = params.get("nights", 1)
        price = params.get("price_per_night", 4500)
        await asyncio.sleep(2.0 / self.speedup)
        return {
            "booking_id": f"HTL_{uuid.uuid4().hex[:6].upper()}",
            "hotel_name": hotel_name,
            "city": city,
            "nights": nights,
            "room_type": "Executive Deluxe Room (Breakfast Included)",
            "total_price": price * nights,
            "status": "CONFIRMED"
        }

    def get_tool_callable(self, tool_name: str):
        mapping = {
            "search_flights": self.search_flights,
            "search_hotels": self.search_hotels,
            "reserve_hotel": self.reserve_hotel,
            "search_cabs": self.search_cabs,
            "check_calendar": self.check_calendar,
            "hold_seat": self.hold_seat,
            "modify_booking": self.modify_booking,
            "confirm_booking": self.confirm_booking,
            "travel_info": self.travel_info,
            "navigate_route": self.navigate_route,
            "set_climate": self.set_climate,
            "reserve_table": self.reserve_table,
            "diagnose_device": self.diagnose_device
        }
        return mapping.get(tool_name, self.travel_info)


mock_sandbox = MockToolSandbox()
mock_sandbox_fast = MockToolSandbox(speedup=50.0)
