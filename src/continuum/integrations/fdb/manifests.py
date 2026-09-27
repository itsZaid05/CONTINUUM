"""The twelve tool contracts used by the released FDB-v3 benchmark.

The schemas intentionally include arguments observed in the released benchmark
that are missing from some upstream Python wrappers (for example
``pets_allowed`` and product ``category``).  CONTINUUM's bridge can therefore
represent every official expected call without source edits.
"""

from __future__ import annotations

from typing import Any

from ...tools import ToolManifest, ToolRegistry


def _object(
    properties: dict[str, dict[str, Any]], required: list[str] | None = None
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required or []),
        "additionalProperties": False,
    }


def _returns(**properties: Any) -> dict[str, Any]:
    """Strict result schema for bridge/backend reconciliation."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


FDB_TOOL_NAMES = (
    "search_flights",
    "book_flight",
    "update_identity_doc",
    "get_card_benefits",
    "get_exchange_rate",
    "modify_autopay",
    "search_apartments",
    "calculate_commute",
    "update_search_filter",
    "track_order",
    "search_products",
    "add_to_cart",
)


def fdb_tool_manifests() -> list[ToolManifest]:
    string = {"type": "string"}
    number = {"type": "number"}
    integer = {"type": "integer", "minimum": 1}
    flexible = {"type": ["string", "number", "integer", "boolean"]}
    apartment = {
        "type": "object",
        "properties": {
            "id": string,
            "address": string,
            "price": number,
            "beds": integer,
            "pets_allowed": {"type": "boolean"},
        },
        "required": ["id", "address", "price", "beds", "pets_allowed"],
        "additionalProperties": False,
    }
    preauthorized = "fdb-v3-simulated-environment"
    return [
        ToolManifest(
            name="search_flights",
            description="Search for available flights to a destination on a date",
            keywords=["flight", "flights", "travel", "fly"],
            domain="travel_identity",
            arguments=_object({"destination": string, "date": string}, ["destination", "date"]),
            returns=_returns(
                status=string,
                flights={
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "flight_id": string,
                            "destination": string,
                            "date": string,
                            "price": number,
                        },
                        "required": ["flight_id", "destination", "date", "price"],
                        "additionalProperties": False,
                    },
                    "minItems": 1,
                },
            ),
        ),
        ToolManifest(
            name="book_flight",
            description="Book a simulated flight ticket for a passenger",
            keywords=["book", "reserve", "ticket", "flight"],
            domain="travel_identity",
            mutation_class="MUTATING",
            cancellable=False,
            idempotent=False,
            authorization=preauthorized,
            postcondition="booking_ref exists",
            arguments=_object({"passenger_name": string, "flight_id": string}, ["passenger_name"]),
            returns=_returns(status=string, booking_ref=string, passenger=string),
        ),
        ToolManifest(
            name="update_identity_doc",
            description="Update simulated identity document details",
            keywords=["passport", "visa", "driver", "license", "identity", "document"],
            domain="travel_identity",
            mutation_class="MUTATING",
            cancellable=False,
            idempotent=True,
            authorization=preauthorized,
            postcondition="updated_doc and masked_number match",
            arguments=_object(
                {"doc_type": string, "doc_number": string}, ["doc_type", "doc_number"]
            ),
            returns=_returns(status=string, updated_doc=string, masked_number=string),
        ),
        ToolManifest(
            name="get_card_benefits",
            description="Get benefits for a simulated credit card type",
            keywords=["card", "credit", "benefit", "cashback", "rewards"],
            domain="finance_billing",
            arguments=_object({"card_type": string}, ["card_type"]),
            returns=_returns(status=string, card_type=string, benefits={"type": "array"}),
        ),
        ToolManifest(
            name="get_exchange_rate",
            description="Convert an amount using the current simulated foreign exchange rate",
            keywords=["exchange", "convert", "currency", "rate", "money"],
            domain="finance_billing",
            arguments=_object(
                {"amount": number, "from_currency": string, "to_currency": string},
                ["amount", "from_currency", "to_currency"],
            ),
            returns=_returns(status=string, converted_amount=number, rate=number),
        ),
        ToolManifest(
            name="modify_autopay",
            description="Modify the source account for a simulated automatic bill payment",
            keywords=["autopay", "automatic", "bill", "payment", "account"],
            domain="finance_billing",
            mutation_class="MUTATING",
            cancellable=False,
            idempotent=True,
            authorization=preauthorized,
            postcondition="bill and source match",
            arguments=_object(
                {"bill_type": string, "source_account": string}, ["bill_type", "source_account"]
            ),
            returns=_returns(
                status=string, autopay_enabled={"type": "boolean"}, bill=string, source=string
            ),
        ),
        ToolManifest(
            name="search_apartments",
            description="Search for rental apartments using the supplied filters",
            keywords=["apartment", "apartments", "rental", "housing", "bedroom", "rent"],
            domain="housing_location",
            arguments=_object(
                {
                    "city": string,
                    "bedrooms": integer,
                    "max_price": number,
                    "pets_allowed": {"type": "boolean"},
                }
            ),
            returns=_returns(
                status=string,
                city=string,
                results={"type": "array", "items": apartment, "minItems": 1},
                apartments={"type": "array", "items": apartment, "minItems": 1},
                cheapest_apartment_address=string,
            ),
        ),
        ToolManifest(
            name="calculate_commute",
            description="Calculate commute time between two addresses",
            keywords=["commute", "travel", "duration", "drive", "walk", "transit", "bike"],
            domain="housing_location",
            arguments=_object(
                {"origin_address": string, "destination_address": string, "mode": string},
                ["origin_address", "destination_address", "mode"],
            ),
            returns=_returns(status=string, duration_mins=number, mode=string),
        ),
        ToolManifest(
            name="update_search_filter",
            description="Update one simulated housing search filter",
            keywords=["filter", "budget", "price", "bedroom", "pets", "neighborhood"],
            domain="housing_location",
            mutation_class="MUTATING",
            cancellable=False,
            idempotent=True,
            authorization=preauthorized,
            postcondition="filter_updated and new_value match",
            arguments=_object({"filter_name": string, "value": flexible}, ["filter_name", "value"]),
            returns=_returns(status=string, filter_updated=string, new_value=flexible),
        ),
        ToolManifest(
            name="track_order",
            description="Track the delivery status of a physical order",
            keywords=["track", "order", "package", "shipping", "delivery"],
            domain="ecommerce_support",
            arguments=_object({"order_id": string}, ["order_id"]),
            returns=_returns(status=string, order_id=string, shipping_status=string),
        ),
        ToolManifest(
            name="search_products",
            description="Search the simulated product catalog",
            keywords=["search", "find", "product", "shop", "buy", "recommend"],
            domain="ecommerce_support",
            arguments=_object(
                {"query": string, "max_price": number, "category": string}, ["query"]
            ),
            returns=_returns(
                status=string,
                products={
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"product_id": string, "name": string, "price": number},
                        "required": ["product_id", "name", "price"],
                        "additionalProperties": False,
                    },
                    "minItems": 1,
                },
                cheapest_product_id=string,
            ),
        ),
        ToolManifest(
            name="add_to_cart",
            description="Add a product and quantity to the simulated shopping cart",
            keywords=["add", "cart", "basket", "product", "quantity"],
            domain="ecommerce_support",
            mutation_class="MUTATING",
            cancellable=False,
            idempotent=False,
            authorization=preauthorized,
            postcondition="product_id and quantity are present in cart",
            arguments=_object(
                {"product_id": string, "quantity": integer}, ["product_id", "quantity"]
            ),
            returns=_returns(status=string, product_id=string, quantity=integer, cart_total=number),
        ),
    ]


def fdb_registry() -> ToolRegistry:
    return ToolRegistry(fdb_tool_manifests())
