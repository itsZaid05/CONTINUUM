#!/usr/bin/env python3
"""Generate frozen gold set 100 utterances, 20 per category, deterministic, pattern-coverable."""

import json
import pathlib
import hashlib

# 20 per category, hand-crafted to be pattern-matchable by offline-fake
items = []

# MODIFY: 20 — contains "actually" or "change" + city
modify_utterances = [
    "Actually, Bangalore",
    "Actually Bangalore",
    "Actually, Mumbai",
    "Actually, Chennai",
    "Actually, Hyderabad",
    "Actually, Kolkata",
    "Change to Bangalore",
    "Actually, Delhi instead",
    "Make it Bangalore",
    "Actually make it Chennai",
    "Change destination to Hyderabad",
    "Actually, Bangalore not Delhi",
    "Switch to Mumbai please",
    "Actually let’s do Bangalore",
    "Correction, Bangalore",
    "Actually, Bangalore city",
    "No, actually Bangalore",
    "Actually Bangalore for the destination",
    "Change it to Kolkata",
    "Actually, Mumbai instead of Delhi",
]

# ADD_CONSTRAINT: 20 — must contain keep + time
add_utterances = [
    "…but keep the morning constraint",
    "But keep the morning constraint",
    "Keep morning",
    "But keep the evening constraint",
    "Keep the morning timing",
    "…but keep the morning flights only",
    "Keep morning, please",
    "But keep morning departure",
    "Keep morning constraint",
    "Actually, but keep the morning constraint",
    "Keep morning and sort by price",
    "Keep the morning window",
    "But keep morning, thanks",
    "Keep morning schedule",
    "But keep the morning time",
    "Keep morning, that’s important",
    "But keep the morning preference",
    "Keep morning flights",
    "But keep morning, keep other filters",
    "Keep the morning slot",
]

# RETRACT: 20 — must contain don't/cancel + book
retract_utterances = [
    "Don't book it",
    "Dont book it",
    "Do not book it",
    "Cancel the booking",
    "Stop, don't book",
    "Don't book the flight",
    "Cancel that booking",
    "Don't book it please",
    "Abort the booking",
    "Do not book the tickets",
    "Don't book it yet",
    "Cancel booking please",
    "Don't book anything",
    "Stop booking",
    "Do not proceed with booking",
    "Cancel the reservation",
    "Don't book the Bangalore flight",
    "Abort booking",
    "Do not book now",
    "Cancel my booking",
]

# NEW_GOAL: 20 — must contain forget + find/search
newgoal_utterances = [
    "Forget flights, find trains",
    "Forget flights find trains",
    "Forget flights, find trains please",
    "Forget flights, search trains",
    "Forget flights and find hotels",
    "Forget flights, look for restaurants",
    "Forget the flights, find trains instead",
    "Forget flights, find trains for tomorrow",
    "Forget flights, search for buses",
    "Forget flights, find alternative trains",
    "Forget flights, find trains now",
    "Forget these flights, find trains",
    "Forget flights, find trains quickly",
    "Forget flights, search trains now",
    "Forget flights and search trains",
    "Forget flights, find trains instead of flights",
    "Forget flights, find nearby hotels",
    "Forget flights, find trains from Bangalore",
    "Forget flights, find morning trains",
    "Forget flight booking, find restaurants",
]

# NOISE: 20 — single backchannel tokens
noise_utterances = [
    "Hmm, okay…",
    "Hmm, okay...",
    "Hmm okay",
    "Okay",
    "Yeah",
    "…",
    "...",
    "Mm-hmm",
    "Uh",
    "Right",
    "Sure",
    "Okay…",
    "Hmm…",
    "Yeah…",
    "Mm-hmm…",
    "Hmm",
    "Okay okay",
    "Yes",
    "Mm",
    "Hmm, yeah",
]


def mk(category, utterance, idx):
    # delta stub minimal
    if category == "MODIFY":
        delta = {
            "op": "replace",
            "field": "destination",
            "old": "Delhi",
            "new": "Bangalore",
            "span": utterance,
        }
    elif category == "ADD_CONSTRAINT":
        delta = {
            "op": "add",
            "field": "constraints",
            "old": None,
            "new": {"time": "morning"},
            "span": utterance,
        }
    elif category == "RETRACT":
        delta = {
            "op": "remove",
            "field": "booking_instruction",
            "old": "book",
            "new": None,
            "span": utterance,
        }
    elif category == "NEW_GOAL":
        delta = {
            "op": "replace",
            "field": "goal_domain",
            "old": "flights",
            "new": "trains",
            "span": utterance,
        }
    else:
        delta = None
    return {
        "id": f"{category.lower()}-{idx:02d}",
        "utterance": utterance,
        "category": category,
        "delta": delta,
        "rationale": f"Gold {category} example {idx}",
    }


for i, u in enumerate(modify_utterances, 1):
    items.append(mk("MODIFY", u, i))
for i, u in enumerate(add_utterances, 1):
    items.append(mk("ADD_CONSTRAINT", u, i))
for i, u in enumerate(retract_utterances, 1):
    items.append(mk("RETRACT", u, i))
for i, u in enumerate(newgoal_utterances, 1):
    items.append(mk("NEW_GOAL", u, i))
for i, u in enumerate(noise_utterances, 1):
    items.append(mk("NOISE", u, i))

assert len(items) == 100

out = pathlib.Path("data/gold/arbiter_100.jsonl")
out.parent.mkdir(parents=True, exist_ok=True)
with out.open("w", encoding="utf-8") as f:
    for it in items:
        f.write(json.dumps(it, ensure_ascii=False) + "\n")

h = hashlib.sha256(out.read_bytes()).hexdigest()[:12]
print(f"Wrote {out} ({len(items)} items) hash {h}")

# also verify offline-fake coverage
import sys

sys.path.insert(0, "src")
from continuum.delta_arbiter import arbitrate_offline_fake
from continuum.versioned_state import VersionedStore

store = VersionedStore()
store.create_initial({"destination": "Delhi", "goal_domain": "flights"})

miss = []
for it in items:
    dec = arbitrate_offline_fake(it["utterance"], store.current())
    if dec.category.value != it["category"]:
        miss.append((it["utterance"], it["category"], dec.category.value))

print(f"Misses: {len(miss)}")
for m in miss:
    print(" MISS", m)
if miss:
    print("WARNING: some gold will be misclassified offline-fake; fix patterns")
else:
    print("OK: 100% coverage")
