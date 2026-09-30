#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Executable value-taking specification for cryptocmd.

This module is the SINGLE SOURCE OF TRUTH for every value-taking rule used by
``core.py`` and ``utils.py``. The runtime code evaluates these rules instead
of hard-coding mappings or boundaries. The replay/audit tool
(``tools/spec_replay.py``) links every printed conclusion back to the clause
identifiers declared in ``CLAUSES`` below.

Clauses
-------
SPEC-COIN-01  canonical coin identity (code + name + aliases)
SPEC-FIAT-01  fiat identifier resolution and priority
SPEC-WIN-01   pagination window semantics (endpoint openness, span, units)
SPEC-COL-01   table columns (canonical headers + source-key aliases)
SPEC-SNAP-01  built-in snapshot schema, age and field defaults
SPEC-SRC-01   live-vs-snapshot precedence (span x age, snapshot wins ties)
SPEC-NORM-01  normalization order: alias normalization before column names
SPEC-CDATA-01 CryptoData legacy constructor signature + alias-field coexist
SPEC-PROV-01  provenance markers (real capture vs spec default vs fallback)
"""

from __future__ import annotations

import datetime as _dt
import json as _json
import os as _os

SPEC_VERSION = "1.0.0"


class SpecError(ValueError):
    """Base class for every specification-level validation error."""


class InvalidParameters(SpecError):
    """Window/request parameters violate SPEC-WIN-01."""


class InvalidCoinCode(SpecError):
    """The requested coin identity cannot be resolved (SPEC-COIN-01)."""


class AmbiguousCoin(InvalidCoinCode):
    """A code maps to several canonical coins and no name was given."""


class InvalidFiat(SpecError):
    """The fiat cannot be resolved by SPEC-FIAT-01."""


class TimestampUnitError(SpecError):
    """Timestamp units are inconsistent or ambiguous (SPEC-WIN-01)."""


# ---------------------------------------------------------------------------
# Clause registry (consumed by the replay tool to render "the basis")
# ---------------------------------------------------------------------------

CLAUSES = {
    "SPEC-COIN-01": (
        "A coin identity is the (canonical_code, canonical_name) pair. A code "
        "matches if it equals the canonical code or any global/per-code alias "
        "(case-insensitive). When a code maps to several canonical coins, the "
        "name must disambiguate via canonical name or a per-code name alias."
    ),
    "SPEC-FIAT-01": (
        "Fiat id resolution priority: (1) exact uppercase code in fiat_ids.json; "
        "(2) documented fiat alias; (3) otherwise raise InvalidFiat. No silent "
        "fallback currency is substituted."
    ),
    "SPEC-WIN-01": (
        "Request windows are half-open on the wire: timeStart EXCLUSIVE, "
        "timeEnd INCLUSIVE, expressed as UTC epoch SECONDS. The user range is a "
        "closed [start,end] of UTC days; wire timeStart=start, timeEnd=end+1d. "
        "Span is (end-start) in days: span==0 (single day) is VALID; span<0 "
        "raises InvalidParameters. Yearly chunks are adjacent half-open windows "
        "(next_start == prev_end), never overlapping and never gapped. "
        "Timestamps auto-scale only ms->s by magnitude; mixed/ambiguous units "
        "raise TimestampUnitError."
    ),
    "SPEC-COL-01": (
        "The table has a fixed canonical column order. Each value is pulled by a "
        "canonical source key; column-name aliases normalize to canonical "
        "headers (case/space/underscore-insensitive). An unknown column falls "
        "back to identity and is provenance-tagged."
    ),
    "SPEC-SNAP-01": (
        "A built-in snapshot is offline historical data. It is usable only when "
        "it covers the requested closed range. Freshness is age = now - "
        "generated_at. Missing fields inside an existing quote are filled with "
        "SPEC defaults and marked snapshot.default; a quote lacking the "
        "mandatory key is dropped and counted. Defaults never masquerade as "
        "real capture."
    ),
    "SPEC-SRC-01": (
        "Precedence is decided jointly by request span and snapshot age. A "
        "snapshot is preferred only when BOTH signals agree (span within "
        "SNAPSHOT_MAX_SPAN_DAYS AND age within SNAPSHOT_MAX_AGE_DAYS); live is "
        "preferred only when BOTH disagree. On ANY disagreement the snapshot "
        "wins and the result is flagged degraded with the conflicting reason."
    ),
    "SPEC-NORM-01": (
        "There is exactly one normalization order for a captured record: "
        "(1) coin alias normalization, then (2) column-name normalization. A "
        "failed mapping takes the fallback branch and carries a source tag, "
        "never silently becoming a canonical value."
    ),
    "SPEC-CDATA-01": (
        "CryptoData keeps the legacy positional constructor "
        "(date, open, high, low, close, volume, market_cap, time_open, "
        "time_high, time_low, time_close) and ADDS keyword-only fields "
        "(alias, canonical_code, canonical_name, source). Old positional call "
        "sites must keep working; any signature break fails whole validation."
    ),
    "SPEC-PROV-01": (
        "Every resolved value carries a provenance tag drawn from "
        "PROVENANCE_TAGS: live.capture, snapshot, snapshot.default, "
        "spec.default, fallback.identity, fallback.literal. Filled defaults are "
        "kept distinct from real captured values."
    ),
}

PROVENANCE_TAGS = (
    "live.capture",
    "snapshot",
    "snapshot.default",
    "spec.default",
    "fallback.identity",
    "fallback.literal",
)


# ---------------------------------------------------------------------------
# SPEC-COIN-01 -- canonical coin identity + alias table
# ---------------------------------------------------------------------------

# Each entry: canonical code -> {"name": canonical name,
#                                "aliases": global code aliases,
#                                "names": per-code name aliases}
CANONICAL_COINS = {
    "BTC": {"name": "Bitcoin", "aliases": ["XBT"], "names": ["bitcoin"]},
    "ETH": {"name": "Ethereum", "aliases": ["ETHER"], "names": ["ethereum"]},
    "SOL": {
        "name": "Solana",
        "aliases": [],
        "names": ["solana", "solcoin"],
    },
    "DOGE": {"name": "Dogecoin", "aliases": ["DOG"], "names": ["dogecoin"]},
    "XRP": {"name": "XRP", "aliases": ["RIPPLE"], "names": ["xrp", "ripple"]},
    "USDT": {
        "name": "Tether",
        "aliases": [],
        "names": ["tether", "tether usdt"],
    },
}


def _key(text):
    """Canonical comparison key: lowercase, trim, collapse inner spaces."""
    return " ".join(str(text).strip().lower().split())


def _coin_indices():
    exact_codes = {}
    alias_to_code = {}
    for code, meta in CANONICAL_COINS.items():
        exact_codes[code.lower()] = code
        for alias in meta.get("aliases", []):
            alias_to_code[_key(alias)] = code
    return exact_codes, alias_to_code


def normalize_coin(coin_code, coin_name=None):
    """Resolve a (code, name) pair to canonical identity.

    Returns a dict with keys:
      canonical_code, canonical_name, alias (or None), source, ambiguous

    ``source`` is one of:
      "canonical"  - code already canonical
      "alias"      - code matched a documented alias (SPEC-COIN-01)
      "fallback.literal" - unknown code; the literal input is preserved, never
                           silently promoted to a canonical code

    Alias normalization is the FIRST stage of the SPEC-NORM-01 pipeline; this
    function never touches column names.
    """
    exact_codes, alias_to_code = _coin_indices()

    requested_code = coin_code
    code_lookup = _key(coin_code) if coin_code is not None else ""

    if code_lookup in exact_codes:
        canonical_code = exact_codes[code_lookup]
        source = "canonical"
        alias = None
    elif code_lookup in alias_to_code:
        canonical_code = alias_to_code[code_lookup]
        source = "alias"
        alias = code_lookup.upper()
    else:
        # Fallback branch (SPEC-NORM-01 / SPEC-PROV-01): keep the literal.
        canonical_code = (
            "".join(code_lookup.split()).upper() if code_lookup else None
        )
        source = "fallback.literal"
        alias = requested_code if requested_code != canonical_code else None

    # Resolve the canonical name for that code.
    meta = CANONICAL_COINS.get(canonical_code)
    ambiguous = False
    canonical_name = None

    if meta is not None:
        canonical_name = meta["name"]
        wanted = _key(coin_name) if coin_name is not None else None
        if wanted and wanted != _key(meta["name"]) and wanted not in [
            _key(n) for n in meta.get("names", [])
        ]:
            # Name does not fit the documented per-code aliases. It is treated
            # as a literal disambiguator and tagged, never overwritten.
            canonical_name = coin_name
            source = "fallback.literal"

    else:
        # Unknown code: retain the literal name when supplied.
        canonical_name = coin_name

    return {
        "canonical_code": canonical_code,
        "canonical_name": canonical_name,
        "alias": alias,
        "source": source,
        "ambiguous": ambiguous,
        "requested_code": requested_code,
        "requested_name": coin_name,
    }


# ---------------------------------------------------------------------------
# SPEC-FIAT-01 -- fiat identifier resolution and priority
# ---------------------------------------------------------------------------

_PACKAGE_DIR = _os.path.dirname(__file__)

with open(_os.path.join(_PACKAGE_DIR, "fiat_ids.json"), encoding="utf-8") as _fh:
    FIAT_IDS = {str(k).upper(): int(v) for k, v in _json.load(_fh).items()}

# Documented fiat aliases (priority stage 2). Aliases never overwrite an exact
# canonical code; they only fill in before raising.
FIAT_ALIASES = {
    "DOLLAR": "USD",
    "EURO": "EUR",
    "€": "EUR",
    "$": "USD",
    "RMB": "CNY",
    "YUAN": "CNY",
    "POUND": "GBP",
    "RUBLE": "RUB",
}

SUPPORTED_FIATS = ", ".join(sorted(FIAT_IDS))


def resolve_fiat(fiat):
    """Resolve a user supplied fiat to its numeric CMC convertId.

    Priority is fixed by SPEC-FIAT-01:
      1. exact uppercase match against fiat_ids.json
      2. documented FIAT_ALIASES entry, re-resolved through stage 1
      3. raise InvalidFiat (no silent fallback currency)

    Returns (convert_id, canonical_fiat, source_tag).
    """
    token = _key(fiat).upper()
    if token in FIAT_IDS:
        return FIAT_IDS[token], token, "canonical"
    if token in FIAT_ALIASES:
        canonical = FIAT_ALIASES[token]
        return FIAT_IDS[canonical], canonical, "alias"
    raise InvalidFiat(
        "Unknown fiat '{0}'. Supported: {1}.".format(fiat, SUPPORTED_FIATS)
    )


# ---------------------------------------------------------------------------
# SPEC-WIN-01 -- pagination window semantics
# ---------------------------------------------------------------------------

INPUT_DATE_FORMAT = "%d-%m-%Y"
API_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
OUTPUT_DATE_FORMAT = "%d-%m-%Y"
UTC = _dt.timezone.utc

# Defaults (moved out of utils.py into the spec).
DEFAULT_START_DATE = "28-04-2013"

# Chunking: maximum span covered by one CMC historical request.
CHUNK_DAYS = 365

# Timestamp magnitude thresholds for unit auto-detection (SPEC-WIN-01).
# Anything >= 10^12 is treated as milliseconds (covers present-day epochs),
# values in the ambiguous microsecond band are rejected outright.
MS_LOWER_BOUND = 10 ** 12
US_LOWER_BOUND = 10 ** 14


def parse_input_date(value):
    """Parse a dd-mm-yyyy input string into a UTC midnight datetime."""
    return _dt.datetime.strptime(value, INPUT_DATE_FORMAT).replace(tzinfo=UTC)


def default_end_date(today=None):
    """Default end date = today (UTC), per the documented scraper behaviour."""
    today = today or _dt.datetime.now(tz=UTC).date()
    return today.strftime(INPUT_DATE_FORMAT)


def normalize_epoch(value):
    """Coerce a wire timestamp to UTC epoch SECONDS with a provenance tag.

    Auto-scales ms -> s by magnitude. Microsecond-scale or larger values are
    ambiguous/unsupported and raise TimestampUnitError so a unit mismatch can
    never silently produce a wrong window.
    """
    number = int(value)
    if number < 0:
        raise TimestampUnitError("Negative epoch timestamp: {0}".format(value))
    if number >= US_LOWER_BOUND:
        raise TimestampUnitError(
            "Timestamp {0} looks like microseconds/nanoseconds; only seconds "
            "and milliseconds are supported (SPEC-WIN-01).".format(value)
        )
    if number >= MS_LOWER_BOUND:
        return number // 1000, "unit.ms"
    return number, "unit.s"


def assert_same_units(start_raw, end_raw):
    """A pair of timestamps must not mix s and ms (SPEC-WIN-01)."""
    _, start_tag = normalize_epoch(start_raw)
    _, end_tag = normalize_epoch(end_raw)
    if start_tag != end_tag:
        raise TimestampUnitError(
            "Mixed timestamp units: timeStart={0} ({1}), timeEnd={2} ({3})".format(
                start_raw, start_tag, end_raw, end_tag
            )
        )


def build_window(start_date, end_date):
    """Turn closed [start_date, end_date] input into a wire half-open window.

    Returns a dict:
      start_dt, end_dt   - UTC datetimes (closed user range, inclusive days)
      span_days          - (end - start) in whole days
      wire_start         - datetime sent as timeStart (EXCLUSIVE on the wire)
      wire_end           - datetime sent as timeEnd (the INCLUSIVE last day)

    Rules (SPEC-WIN-01):
      * span == 0 (single day) is valid;
      * span < 0 raises InvalidParameters;
      * the wire interval is half-open: timeStart EXCLUSIVE, timeEnd INCLUSIVE.
    """
    start_dt = parse_input_date(start_date)
    end_dt = parse_input_date(end_date)
    span_days = (end_dt.date() - start_dt.date()).days
    if span_days < 0:
        raise InvalidParameters(
            "Negative span ({0} days): start_date {1} is after end_date {2} "
            "(SPEC-WIN-01).".format(span_days, start_date, end_date)
        )
    # timeStart is exclusive on the wire, so request from the day BEFORE the
    # first wanted day; timeEnd is the inclusive last wanted day.
    wire_start = start_dt - _dt.timedelta(days=1)
    wire_end = end_dt
    return {
        "start_dt": start_dt,
        "end_dt": end_dt,
        "span_days": span_days,
        "wire_start": wire_start,
        "wire_end": wire_end,
    }


def iter_chunks(window):
    """Yield adjacent half-open (wire_start, wire_end) chunk windows.

    Chunks tile the requested half-open wire interval with
    next_chunk.wire_start == previous_chunk.wire_end, so they neither overlap
    nor leave a gap (SPEC-WIN-01). Spans of 0 and 1 day yield a single chunk.
    """
    start = window["wire_start"]
    end = window["wire_end"]
    cursor = start
    step = _dt.timedelta(days=CHUNK_DAYS)
    while cursor < end:
        chunk_end = min(cursor + step, end)
        yield cursor, chunk_end
        # Adjacency: the exclusive start of the next chunk equals the inclusive
        # end of this chunk (chunk_end), so no overlap and no gap (SPEC-WIN-01).
        cursor = chunk_end


# ---------------------------------------------------------------------------
# SPEC-COL-01 -- table columns and source-key parsing
# ---------------------------------------------------------------------------

# Each tuple: (canonical header, source location, source key, default, aliases)
# location "date"  -> derived from a timestamp via API_TIME_FORMAT
# location "quote" -> numeric field inside the "quote" object
# location "row"   -> timestamp field at the top level of the quote
COLUMNS = [
    ("Date", "date", "timeOpen", None, ["date"]),
    ("Open", "quote", "open", None, ["open", "open_price"]),
    ("High", "quote", "high", None, ["high", "high_price"]),
    ("Low", "quote", "low", None, ["low", "low_price"]),
    ("Close", "quote", "close", None, ["close", "close_price", "closing"]),
    ("Volume", "quote", "volume", None, ["volume", "vol", "volume24h"]),
    ("Market Cap", "quote", "marketCap", None,
     ["market cap", "marketcap", "mcap", "market_cap"]),
    ("Time Open", "row", "timeOpen", None, ["time open", "timeopen"]),
    ("Time High", "row", "timeHigh", None, ["time high", "timehigh"]),
    ("Time Low", "row", "timeLow", None, ["time low", "timelow"]),
    ("Time Close", "row", "timeClose", None, ["time close", "timeclose"]),
]

HEADERS = [col[0] for col in COLUMNS]

# Mandatory key: a quote without it cannot be dated and is dropped (SPEC-SNAP).
MANDATORY_KEY = "timeOpen"

# SPEC-SNAP-01: per-source-key defaults used ONLY to fill missing fields inside
# an existing snapshot quote. They are tagged snapshot.default and therefore can
# never be confused with a real captured value.
SNAPSHOT_DEFAULTS = {
    "open": 0.0,
    "high": 0.0,
    "low": 0.0,
    "close": 0.0,
    "volume": 0.0,
    "marketCap": 0.0,
    "timeHigh": None,
    "timeLow": None,
    "timeClose": None,
}


def _header_key(text):
    """Normalize a header token: lowercase, strip punctuation/underscores."""
    return "".join(ch for ch in str(text).lower() if ch.isalnum() or ch == " ")


def _build_header_index():
    index = {}
    for header, _loc, key, _default, aliases in COLUMNS:
        index[_header_key(header)] = header
        index[_header_key(key)] = header
        for alias in aliases:
            index[_header_key(alias)] = header
    return index


HEADER_INDEX = _build_header_index()


def normalize_column_name(name):
    """Map any header/alias to a canonical table header.

    This is the SECOND and final stage of the SPEC-NORM-01 pipeline (it runs
    strictly after coin alias normalization). An unrecognized column takes the
    fallback branch: the trimmed literal is returned with tag
    ``fallback.identity`` instead of being silently treated as canonical.
    """
    lookup = _header_key(name)
    canonical = HEADER_INDEX.get(lookup)
    if canonical is None:
        return str(name).strip(), "fallback.identity"
    if canonical == name:
        return canonical, "canonical"
    return canonical, "alias"


def _extract(quote, location, key):
    """Return (value, source_tag) for one column from a raw quote dict."""
    if location == "quote":
        container = quote.get("quote") or {}
        if key in container:
            return container[key], "present"
        return None, "missing"
    if location == "row":
        if key in quote and quote[key] is not None:
            return quote[key], "present"
        return None, "missing"
    if location == "date":
        raw = quote.get(key)
        if raw is None:
            return None, "missing"
        try:
            parsed = _dt.datetime.strptime(raw, API_TIME_FORMAT)
        except (TypeError, ValueError):
            # Tolerate a missing fractional-seconds variant.
            parsed = _dt.datetime.strptime(
                raw, "%Y-%m-%dT%H:%M:%SZ"
            )
        return parsed.strftime(OUTPUT_DATE_FORMAT), "present"
    raise SpecError("Unknown column location: {0}".format(location))


def parse_quote_row(quote, source):
    """Parse one raw CMC quote into an ordered, specification-driven row.

    ``source`` is "live" or "snapshot" and selects the missing-field policy:
      * live     -> a missing numeric/ts value stays None, tagged live.capture
                    (the API omitted it; nothing is invented);
      * snapshot -> a missing field is filled with the SPEC default and tagged
                    snapshot.default so a fill can never be mistaken for a real
                    captured value (SPEC-SNAP-01 / SPEC-PROV-01).

    Returns (values, cell_sources, dropped). ``dropped`` is True when the
    mandatory key is absent.
    """
    if not isinstance(quote, dict) or quote.get(MANDATORY_KEY) is None:
        return None, None, True

    values = []
    cell_sources = []
    for header, location, key, default, _aliases in COLUMNS:
        value, state = _extract(quote, location, key)
        if state == "present":
            tag = "snapshot" if source == "snapshot" else "live.capture"
        elif source == "snapshot":
            value = SNAPSHOT_DEFAULTS.get(key, default)
            tag = "snapshot.default"
        else:
            value = None
            tag = "live.capture"
        values.append(value)
        cell_sources.append(tag)
    return values, cell_sources, False


# ---------------------------------------------------------------------------
# SPEC-SNAP-01 / SPEC-SRC-01 -- built-in snapshot + precedence
# ---------------------------------------------------------------------------

SNAPSHOT_FILE = "snapshots.json"

# Decision thresholds (the ONLY boundaries governing precedence).
SNAPSHOT_MAX_SPAN_DAYS = 31
SNAPSHOT_MAX_AGE_DAYS = 14


def load_snapshot_bundle(path=None):
    """Load the built-in offline snapshot bundle.

    Schema: {"generated_at": iso8601 UTC,
             "coins": {canonical_code: {"symbol", "name", "quotes": [...]}}}
    Missing bundle => None (snapshots simply unavailable).
    """
    path = path or _os.path.join(_PACKAGE_DIR, SNAPSHOT_FILE)
    if not _os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return _json.load(handle)


def snapshot_age_days(bundle, now=None):
    """age = now - generated_at, in whole/fractional UTC days."""
    generated = _dt.datetime.strptime(
        bundle["generated_at"], "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=UTC)
    now = now or _dt.datetime.now(tz=UTC)
    return (now - generated).total_seconds() / 86400.0


def snapshot_covers(bundle, canonical_code, window):
    """True if the snapshot has a quote for every closed-range day requested."""
    coins = (bundle or {}).get("coins", {})
    entry = coins.get(canonical_code)
    if not entry:
        return False
    present = set()
    for quote in entry.get("quotes", []):
        raw = quote.get(MANDATORY_KEY)
        if raw is None:
            continue
        day = _dt.datetime.strptime(raw, API_TIME_FORMAT).date()
        present.add(day)
    wanted = set()
    cursor = window["start_dt"].date()
    last = window["end_dt"].date()
    while cursor <= last:
        wanted.add(cursor)
        cursor += _dt.timedelta(days=1)
    return wanted.issubset(present)


def decide_source(span_days, age_days, snapshot_available):
    """Choose 'snapshot' or 'live' from span and age jointly (SPEC-SRC-01).

    Both signals are required and combined; neither dominates alone:
      span within SNAPSHOT_MAX_SPAN_DAYS -> span_votes = snapshot else live
      age  within SNAPSHOT_MAX_AGE_DAYS  -> age_votes  = snapshot else live

    Agreement  -> that source wins.
    Disagreement -> the SNAPSHOT wins (conservative/offline-stable) and the
      result is flagged degraded with the conflicting signal named.
    No snapshot coverage -> live regardless.
    """
    if not snapshot_available:
        return {
            "source": "live",
            "degraded": False,
            "reason": "snapshot-unavailable",
            "span_votes": "live",
            "age_votes": "live" if age_days is None else (
                "snapshot" if age_days <= SNAPSHOT_MAX_AGE_DAYS else "live"
            ),
            "basis": "SPEC-SRC-01",
        }

    span_votes = (
        "snapshot" if span_days <= SNAPSHOT_MAX_SPAN_DAYS else "live"
    )
    age_votes = (
        "snapshot" if age_days <= SNAPSHOT_MAX_AGE_DAYS else "live"
    )

    if span_votes == age_votes:
        # Both agree (snapshot/snapshot or live/live): no conflict.
        return {
            "source": span_votes,
            "degraded": False,
            "reason": "span-and-age-agree",
            "span_votes": span_votes,
            "age_votes": age_votes,
            "basis": "SPEC-SRC-01",
        }

    # Genuine disagreement (one signal live, the other snapshot): the
    # snapshot wins and the result is marked degraded with the signal that
    # favoured live named explicitly (SPEC-SRC-01: conflict -> snapshot wins).
    conflicting = (
        "span" if span_votes == "live" else "age"
    )
    return {
        "source": "snapshot",
        "degraded": True,
        "reason": "conflict-{}-favors-live".format(conflicting),
        "span_votes": span_votes,
        "age_votes": age_votes,
        "basis": "SPEC-SRC-01",
    }


def normalize_record(quote, coin_code, coin_name, source):
    """The single SPEC-NORM-01 pipeline for one captured record.

    Order is fixed and unique: (1) coin alias normalization runs first, then
    (2) column-name/value normalization. Failing mappings go through the
    documented fallback branches and keep their source tags.
    """
    identity = normalize_coin(coin_code, coin_name)
    values, cell_sources, dropped = parse_quote_row(quote, source)
    return {
        "identity": identity,
        "values": values,
        "cell_sources": cell_sources,
        "dropped": dropped,
        "stage_order": ("coin", "column"),
    }
