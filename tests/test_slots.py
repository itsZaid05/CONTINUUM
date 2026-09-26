"""Schema-driven slot extraction: one extractor per JSON-Schema kind."""

from __future__ import annotations

from datetime import date

from continuum.slots import SlotExtractor, lookup_slot, parse_date, parse_time, set_slot
from continuum.tools import ToolManifest

TODAY = date(2026, 9, 25)  # a Friday
X = SlotExtractor(TODAY)


def _m(props: dict, required: list[str] | None = None, **kw) -> ToolManifest:
    return ToolManifest(name=kw.pop("name", "t"), arguments={"type": "object", "properties": props,
                                                            "required": required or []}, **kw)


def test_relative_and_weekday_dates():
    assert parse_date("tomorrow morning", TODAY)[0] == "2026-09-26"
    assert parse_date("the day after tomorrow", TODAY)[0] == "2026-09-27"
    assert parse_date("next Monday", TODAY)[0] == "2026-09-28"
    assert parse_date("on Friday", TODAY)[0] == "2026-10-02"  # same weekday means next week
    assert parse_date("26th Oct", TODAY)[0] == "2026-10-26"


def test_times_normalise_to_24h():
    assert parse_time("at 8pm")[0] == "20:00"
    assert parse_time("3:30 am")[0] == "03:30"
    assert parse_time("around noon")[0] == "12:00"
    assert parse_time("in 2 hours") is None


def test_pattern_conflict_goes_to_the_cued_field():
    m = _m({"model": {"type": "string", "pattern": "^[A-Z]{1,4}-?\\d{2,5}[A-Z]?$"},
            "error_code": {"type": "string", "pattern": "^[A-Z]\\d{1,3}$"}})
    ex = X.extract("What does error E12 mean on the DW-220?", m)
    assert ex["error_code"].value == "E12" and ex["model"].value == "DW-220"


def test_pattern_match_is_case_insensitive_but_canonical():
    m = _m({"ticket_id": {"type": "string", "pattern": "^TCK-\\d{3,6}$"}})
    assert X.extract("status of tck-2231 please", m)["ticket_id"].value == "TCK-2231"


def test_generic_enum_value_needs_its_field_name_nearby():
    m = _m({"priority": {"type": "string", "enum": ["low", "medium", "high"]}})
    assert X.extract("open it with high priority", m)["priority"].value == "high"
    assert "priority" not in X.extract("my battery is running low", m)


def test_enum_later_mention_corrects_earlier_and_keeps_alternative():
    m = _m({"slot": {"type": "string", "enum": ["morning", "evening"]}})
    hit = X.extract("morning or evening, actually evening", m)["slot"]
    assert hit.value == "evening" and hit.alternatives == ["morning"]
    assert X.extract("not morning, evening", m)["slot"].value == "evening"


def test_integer_by_unit_by_count_phrase_and_single_number_fallback():
    temp = _m({"temperature": {"type": "integer", "minimum": 16, "maximum": 30}})
    assert X.extract("set the AC to 22 degrees", temp)["temperature"].value == 22
    assert X.extract("make it 20", temp)["temperature"].value == 20
    assert "temperature" not in X.extract("make it 45", temp)  # outside the schema range
    party = _m({"party_size": {"type": "integer"}, "date": {"type": "string", "format": "date"}})
    ex = X.extract("a table for 2 on 26 Oct", party)
    assert ex["party_size"].value == 2 and ex["date"].value == "2026-10-26"
    qty = _m({"part_number": {"type": "string", "pattern": "^P-\\d{3,6}$"}, "quantity": {"type": "integer"}})
    assert X.extract("Order two P-3321 parts", qty)["quantity"].value == 2  # digits inside the id are not numbers


def test_places_from_prepositions_gazetteer_and_origin():
    m = _m({"to": {"type": "string"}, "from": {"type": "string"}})
    ex = X.extract("Find flights from Mumbai to Chennai on Wednesday", m)
    assert ex["from"].value == "Mumbai" and ex["to"].value == "Chennai"
    assert X.extract("Actually, Bangalore", m)["to"].value == "Bangalore"
    assert X.extract("no, take me to Indiranagar", _m({"destination": {"type": "string"}}))["destination"].value == "Indiranagar"
    assert X.extract("a cab to the airport tomorrow", _m({"to": {"type": "string"}}))["to"].value == "airport"
    assert "city" not in X.extract("a hotel for Saturday", _m({"city": {"type": "string"}}))


def test_free_text_drops_the_command_clause():
    m = ToolManifest(name="create_ticket", keywords=["ticket"], arguments={"type": "object", "properties": {
        "issue": {"type": "string"}, "priority": {"type": "string", "enum": ["low", "high"]}}})
    assert X.free_text("My router keeps dropping wifi, please open a ticket with high priority", "issue", m) == \
        "My router keeps dropping wifi"
    assert X.free_text("Open a ticket", "issue", m) is None
    assert X.free_text("Report a problem: I was charged twice", "issue", m) == "I was charged twice"
    ev = ToolManifest(name="create_event", arguments={"type": "object", "properties": {"title": {"type": "string"}}})
    assert X.free_text("Schedule a meeting with Priya tomorrow at 3pm", "title", ev) == "meeting with Priya"


def test_slot_memory_follows_alias_groups_most_recent_first():
    slots: dict = {}
    set_slot(slots, "to", "Delhi")
    assert lookup_slot(slots, "city") == "Delhi"
    set_slot(slots, "destination", "Goa")
    assert lookup_slot(slots, "to") == "Goa"
    assert lookup_slot(slots, "from") is None
