"""Async, deterministic implementation of the official FDB-v3 mock APIs.

The released benchmark ships synchronous mocks.  This module preserves their
observable values while using ``asyncio.sleep`` so calls can be cancelled,
tracked and cleanly drained by the LiveKit worker.  It also fills the result
aliases used by the released scenario references (``apartments`` and
``cheapest_*``), rather than forcing those references through an incompatible
upstream wrapper shape.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any, Protocol

from .manifests import fdb_registry


class FdbBackend(Protocol):
    """Provider-independent backend boundary used by :class:`FdbToolBridge`."""

    async def call(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]: ...

    async def verify(self, idempotency_key: str) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class LatencyRange:
    minimum_ms: int
    maximum_ms: int
    progressive_factor: float = 1.0


LATENCY_PROFILES: dict[str, LatencyRange] = {
    "instant": LatencyRange(0, 0),
    "fast": LatencyRange(50, 200),
    "normal": LatencyRange(200, 800),
    "slow": LatencyRange(1000, 3000),
    "degraded": LatencyRange(500, 2000, 1.5),
    "timeout_risk": LatencyRange(3000, 8000),
}


class FdbMockBackend:
    """Cancellation-safe counterpart of upstream ``v3/mock_apis.py``.

    A state-changing call is memoized under the bridge's stable effect key.
    The per-key lock closes the concurrent duplicate race.  Read calls are not
    memoized, because repeated reads are independently visible benchmark calls.
    """

    def __init__(self, latency_profile: str = "instant", *, seed: int = 0) -> None:
        if latency_profile not in LATENCY_PROFILES:
            choices = ", ".join(sorted(LATENCY_PROFILES))
            raise ValueError(f"unknown FDB latency profile {latency_profile!r}; choose {choices}")
        self.latency_profile = latency_profile
        self._profile = LATENCY_PROFILES[latency_profile]
        self._random = random.Random(seed)
        self._call_counts: dict[str, int] = {}
        self._effects: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.dispatch_log: list[tuple[str, dict[str, Any], str | None]] = []
        self.injected_delays_ms: list[int] = []
        self._registry = fdb_registry()

    def _delay_ms(self, tool: str) -> int:
        index = self._call_counts.get(tool, 0)
        self._call_counts[tool] = index + 1
        if self._profile.minimum_ms == self._profile.maximum_ms:
            delay = self._profile.minimum_ms
        else:
            delay = self._random.randint(self._profile.minimum_ms, self._profile.maximum_ms)
        if index:
            delay = int(delay * (self._profile.progressive_factor**index))
        self.injected_delays_ms.append(delay)
        return delay

    async def call(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        manifest = self._registry.get(tool)
        clean_args = manifest.validate_args(args)
        if manifest.state_changing and idempotency_key:
            lock = self._locks.setdefault(idempotency_key, asyncio.Lock())
            async with lock:
                cached = self._effects.get(idempotency_key)
                if cached is not None:
                    return dict(cached)
                result = await self._dispatch(tool, clean_args, idempotency_key)
                self._effects[idempotency_key] = dict(result)
                return result
        return await self._dispatch(tool, clean_args, idempotency_key)

    async def _dispatch(
        self, tool: str, args: dict[str, Any], idempotency_key: str | None
    ) -> dict[str, Any]:
        self.dispatch_log.append((tool, dict(args), idempotency_key))
        await asyncio.sleep(self._delay_ms(tool) / 1000.0)

        if tool == "search_flights":
            return {
                "status": "success",
                "flights": [
                    {
                        "flight_id": "FL123",
                        "destination": args["destination"],
                        "date": args["date"],
                        "price": 450.0,
                    }
                ],
            }
        if tool == "book_flight":
            return {
                "status": "success",
                "booking_ref": "B789",
                "passenger": args["passenger_name"],
            }
        if tool == "update_identity_doc":
            number = str(args["doc_number"])
            return {
                "status": "success",
                "updated_doc": args["doc_type"],
                "masked_number": number[-4:],
            }
        if tool == "get_card_benefits":
            return {
                "status": "success",
                "card_type": args["card_type"],
                "benefits": ["2% Cashback", "No Foreign Transaction Fee"],
            }
        if tool == "get_exchange_rate":
            rate = 1.1 if args["from_currency"] == "EUR" else 0.9
            return {
                "status": "success",
                "converted_amount": float(args["amount"]) * rate,
                "rate": rate,
            }
        if tool == "modify_autopay":
            return {
                "status": "success",
                "autopay_enabled": True,
                "bill": args["bill_type"],
                "source": args["source_account"],
            }
        if tool == "search_apartments":
            city = str(args.get("city", "requested area"))
            bedrooms = int(args.get("bedrooms", 1))
            maximum = float(args.get("max_price", 2000.0))
            address = f"100 Main Street, {city}"
            apartments = [
                {
                    "id": "APT1",
                    "address": address,
                    "price": max(0.0, maximum - 100.0),
                    "beds": bedrooms,
                    "pets_allowed": bool(args.get("pets_allowed", True)),
                }
            ]
            return {
                "status": "success",
                "city": city,
                "results": apartments,
                "apartments": apartments,
                "cheapest_apartment_address": address,
            }
        if tool == "calculate_commute":
            return {"status": "success", "duration_mins": 25, "mode": args["mode"]}
        if tool == "update_search_filter":
            return {
                "status": "success",
                "filter_updated": args["filter_name"],
                "new_value": args["value"],
            }
        if tool == "track_order":
            return {
                "status": "success",
                "order_id": args["order_id"],
                "shipping_status": "Out for delivery",
            }
        if tool == "search_products":
            product_maximum: Any = args.get("max_price")
            price = float(product_maximum) - 10.0 if product_maximum is not None else 99.99
            product = {
                "product_id": "PROD1",
                "name": f"{args['query']} Premium",
                "price": price,
            }
            return {
                "status": "success",
                "products": [product],
                "cheapest_product_id": product["product_id"],
            }
        if tool == "add_to_cart":
            quantity = int(args["quantity"])
            return {
                "status": "success",
                "product_id": args["product_id"],
                "quantity": quantity,
                "cart_total": 99.99 * quantity,
            }
        raise KeyError(f"unknown FDB tool {tool!r}")

    async def verify(self, idempotency_key: str) -> dict[str, Any] | None:
        hit = self._effects.get(idempotency_key)
        return dict(hit) if hit is not None else None
