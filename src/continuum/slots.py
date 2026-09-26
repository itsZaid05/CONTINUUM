"""
Schema-driven slot extraction (AI/ML Engineer B).

A scenario hands the agent tool manifests it has never seen, so argument
values cannot come from a domain table. They come from the tool's own JSON
Schema: every ``properties`` entry says what kind of value it wants, and one
extractor per kind looks for it in the utterance —

  enum      the literal enum value (generic adjectives like "high" need the
            property name nearby: "high priority", not "running high")
  pattern   the schema regex, unanchored (``^TCK-\\d+$`` finds "TCK-2231")
  date      today / tomorrow / weekday / ISO / "26 Sep" → ISO date
  time      "8pm" / "20:30" / noon → "HH:MM"
  integer   a number next to one of its units ("22 degrees", "for 4 people"),
            else the only free number when the tool has one integer field
  place     "to/in/near <Capitalised Words>", "from X", a known city anywhere
  free text what is left after the command words are removed
            ("My router keeps dropping wifi, please open a ticket" → the first clause)

Values found in text carry ``evidence=True``: they are what makes one tool a
better reading of the turn than another (``generic_planner`` ranks on it).
Session memory lives in the planner; this module is stateless.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .tools import ToolManifest

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}
MONTH_NAMES = {
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
}  # fmt: skip
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}  # fmt: skip

# Cities a bare correction ("Actually, Bangalore") can name without a preposition.
GAZETTEER = {
    c.lower(): c
    for c in [
        "Delhi", "New Delhi", "Mumbai", "Bangalore", "Bengaluru", "Chennai", "Kolkata",
        "Hyderabad", "Pune", "Goa", "Jaipur", "Ahmedabad", "Kochi", "Lucknow", "Chandigarh",
        "Indore", "Mysore", "Mangalore", "Coimbatore", "Surat", "Nagpur", "Bhopal", "Patna",
        "Varanasi", "Amritsar", "Srinagar", "Guwahati", "Visakhapatnam", "Udaipur", "Agra",
        "Dubai", "Singapore", "London", "Paris", "New York", "Tokyo", "Seoul", "Bangkok",
        "San Francisco",
    ]
}  # fmt: skip

# Argument names that mean the same thing across tools. Session memory is
# keyed by argument name, and a lookup falls back to the most recent value in
# the same group — so ``search_hotels.city`` inherits ``search_flights.to``.
SLOT_GROUPS: dict[str, set[str]] = {
    "place": {"to", "destination", "dest", "city", "location", "place", "near", "where"},
    "origin": {"from", "origin", "source"},
    "date": {"date", "day", "checkin", "check_in", "travel_date", "departure_date"},
    "time_of_day": {"slot", "time_of_day", "period"},
    "time": {"time", "hour"},
    "count": {"party_size", "passengers", "guests", "people", "quantity", "count", "seats"},
    "temperature": {"temperature", "temp"},
    "issue": {"issue", "problem", "description", "summary"},
}
_GROUP_OF = {name: g for g, names in SLOT_GROUPS.items() for name in names}

FREE_TEXT_NAMES = {
    "issue", "problem", "description", "summary", "query", "note", "notes", "title",
    "subject", "message", "text", "reason", "comment",
}  # fmt: skip
_TITLE_NAMES = {"title", "subject", "name"}
_COUNT_UNITS = ["people", "persons", "person", "guests", "passengers", "adults", "seats", "tickets"]
_TEMP_UNITS = ["degrees", "degree", "°"]
# Enum values that are too common to trust without the property name nearby.
_GENERIC_ENUM = {"low", "medium", "high", "all", "none", "yes", "no", "on", "off", "normal"}
_ENUM_SYNONYMS = {"urgent": "high", "asap": "high", "critical": "high", "a/c": "ac"}

STOPWORDS = {
    "a", "an", "the", "i", "me", "my", "we", "us", "our", "you", "your", "it", "its", "is",
    "am", "are", "was", "be", "to", "of", "in", "on", "at", "for", "with", "and", "or", "but",
    "so", "this", "that", "there", "here", "please", "just", "some", "any", "can", "could",
    "would", "will", "should", "do", "does", "did", "have", "has", "need", "want", "like",
    "again", "also", "too", "now", "then", "yet", "up", "down", "out", "about", "since",
    "actually", "okay", "ok", "hmm", "uh", "um", "well", "yes", "no", "not", "don't", "dont",
    "what", "whats", "what's", "which", "who", "when", "where", "why", "let", "lets", "let's",
    "keep", "keeps", "someone", "anything", "something", "thing", "get", "got", "make",
    "instead", "wait", "sure", "right", "them", "they", "he", "she", "it's", "i'm", "i've",
    "one", "via", "by", "from", "into", "as", "if", "all", "very", "really",
}  # fmt: skip
# Words that stop a lowercase place capture ("to the airport tomorrow" → "airport").
_PLACE_STOP = STOPWORDS | {
    "tomorrow", "today", "tonight", "morning", "evening", "afternoon", "night", "avoiding",
    "avoid", "and", "please", "now", "asap", "quickly", *WEEKDAYS,
}  # fmt: skip


def group_of(name: str) -> str:
    return _GROUP_OF.get(name, name)


def lookup_slot(slots: dict[str, Any], name: str) -> Any:
    """Most recent value for ``name`` or any alias in its group (dicts keep
    insertion order; ``set_slot`` re-inserts so the newest key comes last)."""
    group = group_of(name)
    for key in reversed(list(slots)):
        if key == name or group_of(key) == group:
            return slots[key]
    return None


def set_slot(slots: dict[str, Any], name: str, value: Any) -> None:
    slots.pop(name, None)
    slots[name] = value


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9'/-]*", text.lower())


@dataclass
class Extraction:
    value: Any
    kind: str  # enum | pattern | date | time | integer | place | free_text
    evidence: float  # how strongly this hit points at the tool (0 for free text)
    alternatives: list[Any] = field(default_factory=list)
    span: tuple[int, int] | None = None


# ---------------------------------------------------------------------------
# Low-level parsers (date, time, numbers)
# ---------------------------------------------------------------------------


def parse_date(text: str, today: date) -> tuple[str, tuple[int, int]] | None:
    low = text.lower()
    rel = [
        (r"\bday after tomorrow\b", 2),
        (r"\btomorrow\b", 1),
        (r"\b(?:today|tonight)\b", 0),
    ]
    for pat, days in rel:
        m = re.search(pat, low)
        if m:
            return (today + timedelta(days=days)).isoformat(), m.span()
    m = re.search(r"\bthis weekend\b", low)
    if m:
        ahead = (5 - today.weekday()) % 7
        return (today + timedelta(days=ahead)).isoformat(), m.span()
    m = re.search(r"\b(?:next\s+|this\s+|on\s+)?(" + "|".join(WEEKDAYS) + r")\b", low)
    if m:
        ahead = (WEEKDAYS.index(m.group(1)) - today.weekday()) % 7 or 7
        return (today + timedelta(days=ahead)).isoformat(), m.span()
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", low)
    if m:
        return m.group(0), m.span()
    mon = "|".join(MONTHS)
    m = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({mon})[a-z]*\b", low) or re.search(
        rf"\b({mon})[a-z]*\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", low
    )
    if m:
        a, b = m.group(1), m.group(2)
        day, month = (int(a), MONTHS[b[:3]]) if a.isdigit() else (int(b), MONTHS[a[:3]])
        try:
            d = date(today.year, month, day)
        except ValueError:
            return None
        if d < today:
            d = date(today.year + 1, month, day)
        return d.isoformat(), m.span()
    return None


def parse_time(text: str) -> tuple[str, tuple[int, int]] | None:
    low = text.lower()
    m = re.search(r"\b(\d{1,2})(?::([0-5]\d))?\s*(am|pm)\b", low)
    if m:
        hour = int(m.group(1)) % 12 + (12 if m.group(3) == "pm" else 0)
        return f"{hour:02d}:{int(m.group(2) or 0):02d}", m.span()
    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", low)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}", m.span()
    m = re.search(r"\b(noon|midday|midnight)\b", low)
    if m:
        return ("00:00" if m.group(1) == "midnight" else "12:00"), m.span()
    return None


def _numbers(text: str, excluded: list[tuple[int, int]]) -> list[tuple[int, int, int]]:
    """(value, start, end) for free numbers outside already-claimed spans."""
    out = []
    for m in re.finditer(r"\b(\d+|" + "|".join(NUMBER_WORDS) + r")\b", text.lower()):
        s, e = m.span()
        if any(s < xe and e > xs for xs, xe in excluded):
            continue
        tok = m.group(1)
        out.append((int(tok) if tok.isdigit() else NUMBER_WORDS[tok], s, e))
    return out


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


def _is_free_text(name: str, schema: dict[str, Any]) -> bool:
    if schema.get("type", "string") != "string":
        return False
    if any(k in schema for k in ("enum", "pattern", "format")):
        return False
    desc = str(schema.get("description", "")).lower()
    return name in FREE_TEXT_NAMES or "free-text" in desc or "free text" in desc


def _is_place(name: str, schema: dict[str, Any]) -> bool:
    if schema.get("type", "string") != "string" or any(k in schema for k in ("enum", "pattern")):
        return False
    if group_of(name) in {"place", "origin"}:
        return True
    desc = str(schema.get("description", "")).lower()
    return any(w in desc for w in ("city", "place", "destination", "location"))


def _pattern_regex(pattern: str) -> re.Pattern[str]:
    body = pattern.strip()
    body = body[1:] if body.startswith("^") else body
    body = body[:-1] if body.endswith("$") else body
    return re.compile(rf"(?<![\w-])(?:{body})(?![\w-])", re.I)


class SlotExtractor:
    """Fill a manifest's arguments from one utterance (stateless, deterministic)."""

    def __init__(self, today: date | None = None) -> None:
        self.today = today or date.today()

    # ------------------------------------------------------------------ api
    def extract(self, text: str, manifest: ToolManifest) -> dict[str, Extraction]:
        props = manifest.properties
        out: dict[str, Extraction] = {}
        claimed: list[tuple[int, int]] = []

        self._patterns(text, props, out, claimed)
        dt = parse_date(text, self.today)
        tm = parse_time(text)
        for span in (dt[1] if dt else None, tm[1] if tm else None):
            if span:
                claimed.append(span)

        for name, schema in props.items():
            if name in out or not isinstance(schema, dict):
                continue
            hit: Extraction | None = None
            if "enum" in schema:
                hit = self._enum(text, name, schema)
            elif schema.get("format") == "date" or group_of(name) == "date":
                hit = Extraction(dt[0], "date", 0.5, span=dt[1]) if dt else None
            elif schema.get("format") == "time" or group_of(name) == "time":
                hit = Extraction(tm[0], "time", 1.0, span=tm[1]) if tm else None
            elif schema.get("type") in {"integer", "number"}:
                hit = self._integer(text, name, schema, props, claimed)
            elif _is_place(name, schema):
                hit = self._place(text, name)
            if hit is not None:
                out[name] = hit
        return out

    def free_text(self, text: str, name: str, manifest: ToolManifest) -> str | None:
        """What is left of the utterance once the command words are gone."""
        t = text.strip().rstrip("?.! ")
        m = re.search(r"\b(?:saying|that says|which says|to say)\s+(.+)$", t, re.I)
        if m:
            return m.group(1).strip()
        if name in _TITLE_NAMES:
            return self._title(t, manifest)
        if ":" in t:
            after = t.split(":", 1)[1].strip()
            if self._content_words(after, manifest) >= 2:
                return after
        clauses = re.split(r"\s*[,;.!]\s*|\s+(?:and|but|so)\s+", t)
        kept = [c.strip() for c in clauses if self._content_words(c, manifest) >= 2]
        return " ".join(kept) if kept else None

    def is_free_text(self, name: str, manifest: ToolManifest) -> bool:
        schema = manifest.properties.get(name, {})
        return isinstance(schema, dict) and _is_free_text(name, schema)

    # ----------------------------------------------------------- extractors
    def _patterns(
        self,
        text: str,
        props: dict[str, Any],
        out: dict[str, Extraction],
        claimed: list[tuple[int, int]],
    ) -> None:
        # One token can satisfy two patterns ("E12" fits both an error-code and
        # a loose model pattern). Resolve like a matching: the most constrained
        # property picks first, preferring a match its own name cues ("error E12").
        cands: dict[str, list[re.Match[str]]] = {}
        for name, schema in props.items():
            if isinstance(schema, dict) and schema.get("pattern"):
                found = list(_pattern_regex(schema["pattern"]).finditer(text))
                if found:
                    cands[name] = found
        taken: set[tuple[int, int]] = set()
        for name in sorted(cands, key=lambda n: len(cands[n])):
            cues = set(re.split(r"[_\s]+", name.lower()))
            free = [m for m in cands[name] if m.span() not in taken]
            if not free:
                continue
            cued = [m for m in free if cues & set(words(text[max(0, m.start() - 20) : m.start()]))]
            m = (cued or free)[0]
            taken.add(m.span())
            claimed.append(m.span())
            # matched case-insensitively (ASR lowercases "TCK-2231"); restore the
            # canonical case when the schema only admits upper-case letters
            pattern = str(props[name]["pattern"])
            value = m.group(0) if "a-z" in pattern else m.group(0).upper()
            out[name] = Extraction(value, "pattern", 1.0, span=m.span())

    def _enum(self, text: str, name: str, schema: dict[str, Any]) -> Extraction | None:
        low = text.lower()
        for syn, target in _ENUM_SYNONYMS.items():
            low = re.sub(rf"\b{re.escape(syn)}\b", target, low)
        values = [str(v) for v in schema["enum"]]
        hits: list[tuple[int, str]] = []
        for v in values:
            for m in re.finditer(rf"(?<![\w-]){re.escape(v.lower())}(?![\w-])", low):
                hits.append((m.start(), v))
        if not hits:
            return None
        name_tokens = [t for t in re.split(r"[_\s]+", name.lower()) if t]
        if any(v.lower() in _GENERIC_ENUM for _, v in hits):
            near = [
                (pos, v)
                for pos, v in hits
                if v.lower() not in _GENERIC_ENUM
                or any(
                    re.search(rf"\b{re.escape(t)}\b", low[max(0, pos - 25) : pos + len(v) + 25])
                    for t in name_tokens
                )
            ]
            if not near:
                return None
            hits = near
        negated = {
            v
            for pos, v in hits
            if re.search(r"\b(?:not|no|don't|dont|instead of|rather than)\s+(?:\w+\s+)?$", low[:pos])
        }
        ordered = [v for _, v in sorted(hits) if v not in negated] or [v for _, v in sorted(hits)]
        chosen = ordered[-1]  # a later mention corrects an earlier one
        alts = [v for v in dict.fromkeys(ordered) if v != chosen]
        return Extraction(chosen, "enum", 1.0, alternatives=alts)

    def _integer(
        self,
        text: str,
        name: str,
        schema: dict[str, Any],
        props: dict[str, Any],
        claimed: list[tuple[int, int]],
    ) -> Extraction | None:
        nums = _numbers(text, claimed)
        if not nums:
            return None
        low = text.lower()
        name_tokens = [t for t in re.split(r"[_\s]+", name.lower()) if len(t) > 2]
        units = [str(u).lower() for u in schema.get("x-units", [])] + name_tokens
        if group_of(name) == "count":
            units += _COUNT_UNITS
        if group_of(name) == "temperature":
            units += _TEMP_UNITS

        def ok(v: int) -> bool:
            lo, hi = schema.get("minimum"), schema.get("maximum")
            return (lo is None or v >= lo) and (hi is None or v <= hi)

        for v, _s, e in nums:
            after = low[e : e + 24]
            if any(re.match(rf"\s*(?:\w+\s+)?{re.escape(u)}\b", after) for u in units) and ok(v):
                return Extraction(v, "integer", 1.0)
        if group_of(name) == "count":
            for v, s, _e in nums:
                if re.search(r"\b(?:for|of)\s+$", low[max(0, s - 6) : s]) and ok(v) and v <= 30:
                    return Extraction(v, "integer", 1.0)
        for v, s, _e in nums:  # "temperature down to 18": the field named just before
            before = low[max(0, s - 30) : s]
            if any(re.search(rf"\b{re.escape(t)}\b", before) for t in name_tokens) and ok(v):
                return Extraction(v, "integer", 0.8)
        int_props = [
            n for n, sc in props.items() if isinstance(sc, dict) and sc.get("type") in {"integer", "number"}
        ]
        if len(nums) == 1 and int_props == [name] and ok(nums[0][0]):
            return Extraction(nums[0][0], "integer", 0.5)
        return None

    def _place(self, text: str, name: str) -> Extraction | None:
        origin = group_of(name) == "origin"
        found: list[str] = []
        cap = r"((?:[A-Z][\w'-]*)(?:\s+[A-Z][\w'-]*){0,3})"
        preps = r"from" if origin else r"to|towards|in|near|around|at|for"
        for m in re.finditer(rf"\b(?:{preps})\s+(?:the\s+)?{cap}", text):
            val = self._clean_place(m.group(1))
            if val:
                found.append(val)
        if not found and not origin:
            for m in re.finditer(r"\b(?:to|towards|near|around)\s+(?:the\s+)?([a-z][\w'-]*(?:\s+[a-z][\w'-]*){0,2})", text):
                toks = []
                for w in m.group(1).split():
                    if w in _PLACE_STOP:
                        break
                    toks.append(w)
                if toks:
                    found.append(" ".join(toks))
        if not origin:
            origin_vals = {
                self._clean_place(m.group(1)) for m in re.finditer(rf"\bfrom\s+{cap}", text)
            }
            found = [f for f in found if f not in origin_vals]
            if not found:
                low = text.lower()
                for key, city in GAZETTEER.items():
                    hit = re.search(rf"\b{re.escape(key)}\b", low)
                    if hit and not re.search(r"\bfrom\s+$", low[: hit.start()]):
                        found.append(city)
            if not found:
                bare = re.match(r"^\s*(?:no|nope|actually|wait|sorry|rather|make it)[,\s]+" + cap + r"\s*[.!?]?\s*$", text)
                if bare:
                    val = self._clean_place(bare.group(1))
                    if val:
                        found.append(val)
        if not found:
            return None
        return Extraction(found[0], "place", 0.5, alternatives=found[1:])

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _clean_place(raw: str) -> str | None:
        keep = []
        for t in raw.split():
            bare = t.rstrip(".,?!").lower()
            if bare in WEEKDAYS or bare in MONTH_NAMES:
                break
            keep.append(t.rstrip(".,?!"))
        val = " ".join(keep).strip()
        return val if val and val.lower() not in {"i", "me", "the"} else None

    def _content_words(self, clause: str, manifest: ToolManifest) -> int:
        command = set(re.split(r"[_\s]+", manifest.name.lower()))
        command |= {w.lower() for w in manifest.keywords}
        command |= set(words(manifest.description))
        for name, schema in manifest.properties.items():
            command |= set(re.split(r"[_\s]+", name.lower()))
            if isinstance(schema, dict):
                command |= {str(v).lower() for v in schema.get("enum", [])}
        command |= COMMAND_VERBS
        return sum(1 for w in words(clause) if w not in STOPWORDS and w.rstrip("s") not in command and w not in command)

    def _title(self, text: str, manifest: ToolManifest) -> str | None:
        t = text
        spans = [p[1] for p in (parse_date(text, self.today), parse_time(text)) if p]
        for s, e in sorted(spans, reverse=True):  # right to left keeps earlier offsets valid
            t = t[:s] + t[e:]
        t = re.sub(r"\b(?:on|at|for|by)\s*$", "", t.strip())
        t = re.sub(
            r"^\s*(?:please\s+)?(?:can you\s+|could you\s+)?(?:remind me (?:about|to|of)|schedule|create|add|set up|book|put|plan)\s+(?:an?\s+|the\s+)?",
            "",
            t,
            flags=re.I,
        )
        t = re.sub(r"^\s*(?:an?|the)\s+", "", t, flags=re.I)
        t = re.sub(r"\s+", " ", t).strip(" ,.")
        return t or None


# Verbs that only express the command, never the content of a free-text field.
COMMAND_VERBS = {
    "open", "create", "file", "raise", "log", "report", "submit", "book", "reserve", "add",
    "schedule", "order", "buy", "purchase", "pay", "confirm", "set", "start", "change",
    "update", "modify", "cancel", "delete", "remove", "close", "find", "search", "show",
    "check", "look", "list", "tell", "help", "please", "ticket", "complaint",
}  # fmt: skip
