"""Manifest-driven planner: ranking, chaining, goal handling, gating."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from continuum.contracts import ArbiterCategory, ArbiterDecision, RiskLevel
from continuum.generic_planner import GenericPlanner, parse_cues
from continuum.tools import ToolManifest, ToolRegistry, default_registry, load_manifests

TODAY = date(2026, 9, 25)
MANIFESTS = Path("data/manifests")


def planner(*domains: str) -> GenericPlanner:
    reg = ToolRegistry()
    for d in domains:
        for m in default_registry().all() if d == "builtin" else load_manifests(MANIFESTS / f"{d}.json"):
            reg.register(m)
    return GenericPlanner(reg, today=TODAY)


def test_booking_chains_through_declared_return_fields():
    d = planner("builtin").plan("Book Delhi flights for next Monday morning")
    assert d.tools == ["search_flights", "hold_seat", "confirm_booking"]
    assert d.args_for("search_flights") == {"to": "Delhi", "date": "2026-09-28", "slot": "morning"}
    assert d.args_for("hold_seat") == {"flight_id": "ref(search_flights.flight_id)"}
    assert d.args_for("confirm_booking") == {"hold_id": "ref(hold_seat.hold_id)"}
    assert d.needs_confirmation == []  # "book" is an explicit commit


def test_information_question_never_selects_a_write_tool():
    d = planner("support").plan("any update on my complaint?", slots={"ticket_id": "TCK-3301"})
    assert d.goals == ["get_ticket_status"] and not d.state_changing


def test_correction_keeps_goal_and_other_slots():
    d = planner("builtin").plan(
        "Actually, Bangalore", slots={"to": "Delhi", "slot": "morning"}, goals=["search_flights"]
    )
    assert d.mode == "modify"
    assert d.args_for("search_flights") == {"to": "Bangalore", "slot": "morning"}


def test_additive_goal_keeps_the_running_one():
    d = planner("builtin").plan("also find a hotel there", slots={"to": "Goa"}, goals=["search_flights"])
    assert d.mode == "additive" and d.goals == ["search_flights", "search_hotels"]
    assert d.args_for("search_hotels") == {"city": "Goa"}


def test_replace_marker_drops_the_old_goal():
    d = planner("builtin").plan("Forget flights, find me a hotel in Jaipur instead",
                                slots={"to": "Delhi"}, goals=["search_flights"])
    assert d.goals == ["search_hotels"] and d.args_for("search_hotels") == {"city": "Jaipur"}


def test_retraction_keeps_only_the_read_only_prefix():
    dec = ArbiterDecision(category=ArbiterCategory.RETRACT, confidence=0.9, rationale="t")
    d = planner("builtin").plan("Don't book it, just show me the options", decision=dec,
                                slots={"to": "Delhi"}, goals=["confirm_booking"])
    assert d.mode == "retract" and d.tools == ["search_flights"] and not d.state_changing


def test_negated_commit_verb_stops_before_the_irreversible_step():
    d = planner("builtin").plan("Hold a seat on a flight to Goa but don't confirm it yet")
    assert d.tools == ["search_flights", "hold_seat"]


def test_missing_required_argument_asks_instead_of_guessing():
    d = planner("support").plan("Open a ticket")
    assert d.action == "clarify" and d.missing == [("create_ticket", "issue")]
    assert "issue" in (d.question or "")


def test_pending_clarification_is_answered_by_the_next_turn():
    p = planner("support")
    d = p.plan("the printer on floor two is jammed", goals=["create_ticket"], pending=("create_ticket", "issue"))
    assert d.action == "plan" and "printer" in d.args_for("create_ticket")["issue"]


def test_irreversible_without_commit_verb_needs_confirmation():
    d = planner("troubleshooting").plan("can someone come and repair it on Monday?", slots={"model": "WM-4500"})
    assert d.goals == ["schedule_technician"] and d.needs_confirmation == ["schedule_technician"]


def test_committed_goal_is_updated_not_recreated():
    d = planner("support").plan("make it high priority", slots={"ticket_id": "TCK-1042"},
                                goals=["create_ticket"], committed={"create_ticket"})
    assert d.goals == ["update_ticket"]
    assert d.args_for("update_ticket") == {"ticket_id": "TCK-1042", "priority": "high"}


def test_backchannel_is_a_noop():
    noise = ArbiterDecision(category=ArbiterCategory.NOISE, confidence=0.96, rationale="t")
    d = planner("builtin").plan("hmm okay", decision=noise, goals=["search_flights"])
    assert d.action == "noop" and d.goals == ["search_flights"]


def test_state_changing_tools_are_never_used_as_producers():
    reg = ToolRegistry([
        ToolManifest(name="make_widget", mutation_class="MUTATING", returns={"properties": {"widget_id": {}}}),
        ToolManifest(name="show_widget", arguments={"type": "object", "properties": {"widget_id": {"type": "string"}},
                                                    "required": ["widget_id"]}),
    ])
    d = GenericPlanner(reg, today=TODAY).plan("show widget")
    assert d.tools == ["show_widget"] and d.missing == [("show_widget", "widget_id")]


def test_unknown_request_asks_rather_than_acting():
    d = planner("support").plan("the weather is lovely")
    assert d.action == "clarify" and d.steps == []


def test_cues_parse_negation_and_request_forms():
    c = parse_cues("Don't book it, just show me the options")
    assert "book" in c.negated and "read" in c.verbs and "create" not in c.verbs
    assert parse_cues("can someone come and repair it?").request
    assert parse_cues("any update on my complaint?").question


@pytest.mark.parametrize(
    "raw,expected",
    [({"read_only": True}, "READ_ONLY"), ({"state_modifying": True}, "MUTATING"),
     ({"mutation_class": "state-modifying"}, "MUTATING"), ({"mutation_class": "irreversible"}, "IRREVERSIBLE")],
)
def test_manifest_mutation_vocabulary_is_normalised(raw, expected):
    m = ToolManifest.model_validate({"name": "x", **raw})
    assert m.mutation_class == expected
    assert (m.risk == RiskLevel.FREE) == (expected == "READ_ONLY")


def test_invalid_mutation_class_is_rejected():
    with pytest.raises(ValueError):
        ToolManifest(name="x", mutation_class="sometimes")
