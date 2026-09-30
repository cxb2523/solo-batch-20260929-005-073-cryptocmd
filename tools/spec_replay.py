#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Replay/audit tool for the cryptocmd executable specification.

It replays historical (recorded) CMC responses through BOTH value-taking
paths -- the live-capture path and the built-in snapshot path -- prints each
path's conclusion case by case, and renders a comparison HTML page that links
every conclusion to the exact spec clause and the function that implements it.

Usage:
    python tools/spec_replay.py             # run, print + write report
    python tools/spec_replay.py --strict    # exit non-zero on any mismatch and
                                            # compare against the baseline
    python tools/spec_replay.py --regen     # rewrite the committed baseline
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import sys
from unittest.mock import MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cryptocmd import spec  # noqa: E402
from cryptocmd.core import CryptoData  # noqa: E402
from cryptocmd.utils import download_coin_data  # noqa: E402

UTC = dt.timezone.utc
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(TOOLS_DIR, "replay_out")
REPORT_PATH = os.path.join(OUT_DIR, "replay_report.html")
RESULT_PATH = os.path.join(OUT_DIR, "replay_results.json")
BASELINE_PATH = os.path.join(TOOLS_DIR, "replay_baseline.json")


# ---------------------------------------------------------------------------
# Deterministic "historical" dataset (a recorded slice of CMC-style answers)
# ---------------------------------------------------------------------------

def _quote(day, quote=None, times=None):
    quote = quote or {
        "open": 100.0,
        "high": 110.0,
        "low": 95.0,
        "close": 105.0,
        "volume": 1000.0,
        "marketCap": 1_000_000.0,
    }
    times = times or {}
    return {
        "timeOpen": "{0}T00:00:00.000Z".format(day),
        "timeHigh": times.get("timeHigh", "{0}T12:00:00.000Z".format(day)),
        "timeLow": times.get("timeLow", "{0}T06:00:00.000Z".format(day)),
        "timeClose": times.get(
            "timeClose", "{0}T23:59:59.999Z".format(day)
        ),
        "quote": dict(quote),
    }


HISTORICAL_QUOTES = {
    "2024-01-19": _quote("2024-01-19"),
    "2024-01-20": _quote(
        "2024-01-20",
        {"open": 1, "high": 2, "low": 1, "close": 2, "volume": 5,
         "marketCap": 9},
    ),
    "2024-01-21": _quote(
        "2024-01-21",
        {"open": 2, "high": 3, "low": 2, "close": 3, "volume": 6,
         "marketCap": 10},
    ),
    "2024-01-22": _quote(
        "2024-01-22",
        {"open": 3, "high": 4, "low": 3, "close": 4, "volume": 7,
         "marketCap": 11},
    ),
    "2024-01-23": _quote(
        "2024-01-23",
        {"open": 4, "high": 5, "low": 4, "close": 5, "volume": 8,
         "marketCap": 12},
    ),
    "2024-01-24": _quote(
        "2024-01-24",
        {"open": 5, "high": 6, "low": 5, "close": 6, "volume": 9,
         "marketCap": 13},
    ),
    "2024-01-25": _quote(
        "2024-01-25",
        {"open": 6, "high": 7, "low": 6, "close": 7, "volume": 10,
         "marketCap": 14},
    ),
    "2024-01-26": _quote(
        "2024-01-26",
        {"open": 7, "high": 8, "low": 7, "close": 8, "volume": 11,
        "marketCap": 15},
    ),
}


def _response(payload):
    mock = MagicMock()
    mock.json.return_value = payload
    return mock


def _contiguous_snapshot(start, end, generated_at="2024-01-25T00:00:00Z",
                         code="BTC", name="Bitcoin"):
    """Build a snapshot bundle that fully covers [start, end] (dd-mm-yyyy)."""
    cur = dt.datetime.strptime(start, "%d-%m-%Y").date()
    last = dt.datetime.strptime(end, "%d-%m-%Y").date()
    quotes = []
    while cur <= last:
        iso = cur.isoformat()
        day = {
            "timeOpen": "{0}T00:00:00.000Z".format(iso),
            "timeHigh": "{0}T12:00:00.000Z".format(iso),
            "timeLow": "{0}T06:00:00.000Z".format(iso),
            "timeClose": "{0}T23:59:59.999Z".format(iso),
            "quote": {
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1.0,
                "marketCap": 1.0,
            },
        }
        quotes.append(day)
        cur += dt.timedelta(days=1)
    return {
        "generated_at": generated_at,
        "coins": {code: {"symbol": code, "name": name, "quotes": quotes}},
    }


def make_historical_transport(quotes_by_day=None, listing=None,
                              ms_timestamps=False):
    """Build a deterministic fake CMC transport from recorded responses.

    It honours SPEC-WIN-01 half-open filtering: a quote is returned when
    timeStart < candle_midnight <= timeEnd. If ``ms_timestamps`` is set the
    request params are expressed in milliseconds to exercise unit handling.
    """
    quotes_by_day = quotes_by_day or HISTORICAL_QUOTES
    listing = listing or {
        "status": {"error_code": 0},
        "data": {
            "cryptoCurrencyList": [
                {"id": 1, "symbol": "BTC", "name": "Bitcoin"},
                {"id": 2, "symbol": "ETH", "name": "Ethereum"},
            ]
        },
    }

    def transport(url):
        if "listing" in url:
            return _response(listing)
        params = dict(
            pair.split("=", 1) for pair in url.split("?", 1)[1].split("&")
        )
        divisor = 1000 if ms_timestamps else 1
        start = int(params["timeStart"]) // divisor
        end = int(params["timeEnd"]) // divisor
        out = []
        for day, quote in sorted(quotes_by_day.items()):
            midnight = int(
                dt.datetime.strptime(day, "%Y-%m-%d")
                .replace(tzinfo=UTC)
                .timestamp()
            )
            if start < midnight <= end:
                out.append(quote)
        return _response(
            {
                "status": {"error_code": 0, "error_message": None},
                "data": {
                    "id": int(params.get("id", 1)),
                    "symbol": "BTC",
                    "name": "Bitcoin",
                    "quotes": out,
                },
            }
        )

    return transport


def _run_path(case, forced, now):
    """Execute one value-taking path and return a summarized conclusion."""
    transport = make_historical_transport(
        ms_timestamps=case.get("ms_timestamps", False)
    )
    bundle = case.get("snapshot_bundle")
    if bundle == "_contiguous_":
        bundle = _contiguous_snapshot(case["start"], case["end"])
    try:
        envelope = download_coin_data(
            case.get("coin_code", "BTC"),
            case.get("start"),
            case.get("end"),
            case.get("fiat", "USD"),
            case.get("coin_name"),
            id_number=case.get("id_number"),
            transport=transport,
            now=now,
            snapshot_bundle=bundle,
            force_source=forced,
        )
    except Exception as exc:  # noqa: BLE001 - recorded as a conclusion
        return {"error": type(exc).__name__, "message": str(exc)}

    prov = envelope.get("__provenance__", {})
    data = envelope.get("data", {})
    rows = []
    for quote in data.get("quotes", []):
        values, tags, dropped = spec.parse_quote_row(
            quote, "snapshot" if forced == "snapshot" else "live"
        )
        if not dropped:
            rows.append(
                {
                    "date": values[0],
                    "close": values[4],
                    "volume": values[5],
                    "tags": tags,
                }
            )
    return {
        "source": prov.get("source"),
        "degraded": prov.get("degraded"),
        "reason": prov.get("reason"),
        "basis": prov.get("basis"),
        "span_days": prov.get("span_days"),
        "age_days": prov.get("snapshot_age_days"),
        "covered": prov.get("snapshot_covered"),
        "span_votes": prov.get("span_votes"),
        "age_votes": prov.get("age_votes"),
        "fills": prov.get("snapshot_fills"),
        "dropped": prov.get("snapshot_dropped"),
        "fiat": prov.get("fiat"),
        "identity": prov.get("coin"),
        "window": prov.get("window"),
        "chunks": prov.get("chunk_boundaries"),
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Case catalogue. Each case runs BOTH paths and asserts spec expectations.
# ---------------------------------------------------------------------------

NOW_FRESH = dt.datetime(2024, 1, 26, tzinfo=UTC)   # snapshot age = 1 day
NOW_STALE = dt.datetime(2026, 1, 26, tzinfo=UTC)   # snapshot age ~ 731 days


def _cases():
    return [
        {
            "id": "C01-span-age-agree-snapshot",
            "title": "Span and age both favour snapshot",
            "now": "2024-01-26",
            "start": "20-01-2024",
            "end": "25-01-2024",
            "clause": "SPEC-SRC-01",
            "func": "cryptocmd.spec.decide_source",
            "expect": {"source": "snapshot", "degraded": False},
        },
        {
            "id": "C02-span-age-agree-live",
            "title": "Long span and stale age both favour live",
            "now": "2026-01-26",
            "start": "28-04-2013",
            "end": "25-01-2024",
            "clause": "SPEC-SRC-01",
            "func": "cryptocmd.spec.decide_source",
            "expect": {"source": "live", "degraded": False},
        },
        {
            "id": "C03-conflict-span-live-age-snap",
            "title": "Conflict: span favours live, age favours snapshot",
            "now": "2024-01-26",
            "start": "01-10-2023",
            "end": "25-01-2024",
            "clause": "SPEC-SRC-01",
            "func": "cryptocmd.spec.decide_source",
            "snapshot_bundle": "_contiguous_",
            "expect": {"source": "snapshot", "degraded": True,
                       "reason_prefix": "conflict-span"},
        },
        {
            "id": "C04-conflict-span-snap-age-live",
            "title": "Conflict: span favours snapshot, age favours live",
            "now": "2026-01-26",
            "start": "20-01-2024",
            "end": "25-01-2024",
            "clause": "SPEC-SRC-01",
            "func": "cryptocmd.spec.decide_source",
            "expect": {"source": "snapshot", "degraded": True,
                       "reason_prefix": "conflict-age"},
        },
        {
            "id": "C05-snapshot-coverage-missing",
            "title": "Fresh but snapshot lacks the requested range -> live",
            "now": "2024-01-26",
            "start": "01-01-2020",
            "end": "02-01-2020",
            "clause": "SPEC-SNAP-01",
            "func": "cryptocmd.spec.snapshot_covers",
            "expect": {"source": "live", "degraded": False,
                       "covered": False},
        },
        {
            "id": "C06-snapshot-default-fill",
            "title": "Snapshot missing fields filled + tagged, undateable dropped",
            "now": "2024-01-26",
            "start": "20-01-2024",
            "end": "25-01-2024",
            "clause": "SPEC-SNAP-01",
            "func": "cryptocmd.spec.parse_quote_row",
            "path": "snapshot",
            "expect": {"fills": 3, "dropped": 1},
        },
        {
            "id": "C07-coin-alias-normalization",
            "title": "Coin alias XBT resolves to canonical BTC (first stage)",
            "now": "2024-01-26",
            "coin_code": "xbt",
            "start": "20-01-2024",
            "end": "20-01-2024",
            "clause": "SPEC-COIN-01",
            "func": "cryptocmd.spec.normalize_coin",
            "expect": {"identity_code": "BTC", "identity_source": "alias",
                       "identity_alias": "XBT"},
        },
        {
            "id": "C08-column-alias-normalization",
            "title": "Column aliases map to canonical headers (second stage)",
            "now": "2024-01-26",
            "start": "20-01-2024",
            "end": "20-01-2024",
            "clause": "SPEC-COL-01",
            "func": "cryptocmd.spec.normalize_column_name",
            "unit": "column",
        },
        {
            "id": "C09-span-zero-valid",
            "title": "Zero span (single day) is valid; half-open wire window",
            "now": "2024-01-26",
            "start": "20-01-2024",
            "end": "20-01-2024",
            "clause": "SPEC-WIN-01",
            "func": "cryptocmd.spec.build_window",
            "path": "live",
            "expect": {"span_days": 0},
        },
        {
            "id": "C10-span-negative-rejected",
            "title": "Negative span raises InvalidParameters",
            "now": "2024-01-26",
            "start": "25-01-2024",
            "end": "20-01-2024",
            "clause": "SPEC-WIN-01",
            "func": "cryptocmd.spec.build_window",
            "path": "live",
            "expect_error": "InvalidParameters",
        },
        {
            "id": "C11-timestamp-ms-scaled",
            "title": "Timestamp units: seconds, ms->s scaling, mixed rejected",
            "clause": "SPEC-WIN-01",
            "func": "cryptocmd.spec.normalize_epoch",
            "unit": "timestamp",
        },
        {
            "id": "C14-window-half-open",
            "title": "Closed range maps to half-open wire endpoints; span rules",
            "clause": "SPEC-WIN-01",
            "func": "cryptocmd.spec.build_window",
            "unit": "window",
        },
        {
            "id": "C12-fiat-alias-priority",
            "title": "Fiat alias EURO resolves through priority to EUR",
            "now": "2024-01-26",
            "start": "20-01-2024",
            "end": "20-01-2024",
            "fiat": "euro",
            "clause": "SPEC-FIAT-01",
            "func": "cryptocmd.spec.resolve_fiat",
            "path": "live",
            "expect": {"fiat_code": "EUR", "fiat_source": "alias"},
        },
        {
            "id": "C13-unknown-fiat-rejected",
            "title": "Unknown fiat raises InvalidFiat (no silent fallback)",
            "now": "2024-01-26",
            "start": "20-01-2024",
            "end": "20-01-2024",
            "fiat": "ZZZ",
            "clause": "SPEC-FIAT-01",
            "func": "cryptocmd.spec.resolve_fiat",
            "path": "live",
            "expect_error": "InvalidFiat",
        },
    ]


def check_cryptodata_compatibility():
    """SPEC-CDATA-01 gate: legacy positional signature + new alias fields.

    Any break here fails the entire strict validation run.
    """
    legacy_args = (
        "25-04-2026", 1.0, 2.0, 0.5, 1.5, 100.0, 1000.0,
        "t1", "t2", "t3", "t4",
    )
    record = CryptoData(*legacy_args)
    positional_ok = record.as_row() == list(legacy_args)

    enhanced = CryptoData(
        *legacy_args,
        alias="XBT",
        canonical_code="BTC",
        canonical_name="Bitcoin",
        source="snapshot",
        cell_sources=["snapshot"],
    )
    alias_ok = (
        enhanced.alias == "XBT"
        and enhanced.canonical_code == "BTC"
        and enhanced.source == "snapshot"
    )
    from_row = CryptoData.from_row(
        list(legacy_args),
        identity={
            "alias": None,
            "canonical_code": "BTC",
            "canonical_name": "Bitcoin",
        },
        source="live",
        cell_sources=["live.capture"],
    )
    from_row_ok = from_row.as_row() == list(legacy_args)
    passed = positional_ok and alias_ok and from_row_ok
    return {
        "id": "C00-cryptodata-compatibility",
        "title": "CryptoData legacy signature + alias fields coexist",
        "clause": "SPEC-CDATA-01",
        "func": "cryptocmd.core.CryptoData",
        "passed": passed,
        "checks": {
            "legacy_positional": positional_ok,
            "new_alias_fields": alias_ok,
            "from_row": from_row_ok,
        },
    }


def check_normalization_order():
    """SPEC-NORM-01: one fixed order (coin then column) + fallback sourcing."""
    pipeline = spec.normalize_record(
        _quote("2024-01-20"), "xbt", None, "live"
    )
    order_ok = pipeline["stage_order"] == ("coin", "column")
    coin_ok = (
        pipeline["identity"]["canonical_code"] == "BTC"
        and pipeline["identity"]["source"] == "alias"
    )
    canon_header, _canon_tag = spec.normalize_column_name("Market_Cap")
    fallback_header, fallback_tag = spec.normalize_column_name("Weird X!")
    column_ok = (
        canon_header == "Market Cap"
        and fallback_tag == "fallback.identity"
        and fallback_header == "Weird X!"
    )
    literal_ok = spec.normalize_coin("zzz")["source"] == "fallback.literal"
    passed = order_ok and coin_ok and column_ok and literal_ok
    return {
        "id": "C00b-normalization-order",
        "title": "Alias-before-column order with tagged fallbacks",
        "clause": "SPEC-NORM-01",
        "func": "cryptocmd.spec.normalize_record",
        "passed": passed,
        "checks": {
            "stage_order_coin_then_column": order_ok,
            "coin_alias_to_canonical": coin_ok,
            "column_alias_and_identity_fallback": column_ok,
            "unknown_coin_literal_fallback": literal_ok,
        },
    }


def _evaluate(case, result):
    """Apply the case expectation to a single path's result dict."""
    checks = []

    if case.get("expect_error"):
        passed = result.get("error") == case["expect_error"]
        checks.append(
            "raised {0} = {1}".format(case["expect_error"], passed)
        )
        return passed, checks

    expect = case.get("expect", {})
    for key, expected in expect.items():
        if key == "reason_prefix":
            actual = result.get("reason") or ""
            ok = actual.startswith(expected)
        elif key == "identity_code":
            actual = result.get("identity", {}).get("canonical_code")
            ok = actual == expected
        elif key == "identity_source":
            actual = result.get("identity", {}).get("source")
            ok = actual == expected
        elif key == "identity_alias":
            actual = result.get("identity", {}).get("alias")
            ok = actual == expected
        elif key == "fiat_code":
            actual = (result.get("fiat") or {}).get("code")
            ok = actual == expected
        elif key == "fiat_source":
            actual = (result.get("fiat") or {}).get("source")
            ok = actual == expected
        else:
            actual = result.get(key)
            ok = actual == expected
        checks.append("{0}: {1!r}=={2!r} -> {3}".format(key, actual, expected, ok))

    return all("-> True" in c for c in checks) if checks else True, checks


def _column_unit_case():
    """Pure SPEC-COL-01 unit check rendered like a two-path case."""
    examples = {
        "Open": ("Open", "canonical"),
        "market_cap": ("Market Cap", "alias"),
        "Closing": ("Close", "alias"),
        "VOL": ("Volume", "alias"),
        "???": ("???", "fallback.identity"),
    }
    rows = []
    passed = True
    for given, (want_header, want_tag) in examples.items():
        header, tag = spec.normalize_column_name(given)
        ok = header == want_header and tag == want_tag
        passed = passed and ok
        rows.append(
            {"given": given, "header": header, "tag": tag, "ok": ok}
        )
    return {"passed": passed, "rows": rows}


def _timestamp_unit_case():
    """SPEC-WIN-01 timestamp units: s, ms->s scaling, mixed rejected."""
    seconds_value, seconds_tag = spec.normalize_epoch(1705756800)
    ms_value, ms_tag = spec.normalize_epoch(1705756800000)
    try:
        spec.assert_same_units(1705756800, 1705756800000)
        mixed_ok = False
    except spec.TimestampUnitError:
        mixed_ok = True
    try:
        spec.normalize_epoch(10 ** 15)
        us_ok = False
    except spec.TimestampUnitError:
        us_ok = True
    rows = [
        {"given": "1705756800 (s)", "header": str(seconds_value),
         "tag": seconds_tag, "ok": seconds_tag == "unit.s"},
        {"given": "1705756800000 (ms)", "header": str(ms_value),
         "tag": ms_tag,
         "ok": ms_tag == "unit.ms" and ms_value == 1705756800},
        {"given": "mixed s+ms pair", "header": "TimestampUnitError",
         "tag": "rejected", "ok": mixed_ok},
        {"given": "10^15 (micro/nano)", "header": "TimestampUnitError",
         "tag": "rejected", "ok": us_ok},
    ]
    return {"passed": all(r["ok"] for r in rows), "rows": rows}


def _window_unit_case():
    """SPEC-WIN-01 closed user range -> half-open wire window, span rules."""
    window = spec.build_window("20-01-2024", "25-01-2024")
    single = spec.build_window("20-01-2024", "20-01-2024")
    chunks = list(spec.iter_chunks(spec.build_window("28-04-2013", "25-01-2024")))
    adjacent = all(
        chunks[i][1] == chunks[i + 1][0] for i in range(len(chunks) - 1)
    )
    rows = [
        {"given": "span [20..25]", "header": "{0}d".format(window["span_days"]),
         "tag": "span", "ok": window["span_days"] == 5},
        {"given": "span single day", "header": "{0}d".format(single["span_days"]),
         "tag": "span-zero", "ok": single["span_days"] == 0},
        {
            "given": "timeStart exclusive (day before)",
            "header": int(window["wire_start"].timestamp()),
            "tag": "endpoint",
            "ok": int(window["wire_start"].timestamp())
            == int(window["start_dt"].timestamp()) - 86400,
        },
        {
            "given": "timeEnd inclusive (last day)",
            "header": int(window["wire_end"].timestamp()),
            "tag": "endpoint",
            "ok": int(window["wire_end"].timestamp())
            == int(window["end_dt"].timestamp()),
        },
        {"given": "yearly chunks adjacent, no overlap/gap",
         "header": "{0} chunks".format(len(chunks)),
         "tag": "chunk", "ok": adjacent and len(chunks) > 1},
    ]
    return {"passed": all(r["ok"] for r in rows), "rows": rows}


def run_cases():
    """Run every case through both paths and evaluate expectations."""
    results = []

    results.extend(
        [check_cryptodata_compatibility(), check_normalization_order()]
    )

    for case in _cases():
        entry = {
            "id": case["id"],
            "title": case["title"],
            "clause": case["clause"],
            "func": case["func"],
            "clause_text": spec.CLAUSES[case["clause"]],
        }

        if case.get("unit") in ("column", "timestamp", "window"):
            unit = {
                "column": _column_unit_case,
                "timestamp": _timestamp_unit_case,
                "window": _window_unit_case,
            }[case["unit"]]()
            entry["unit"] = unit
            entry["passed"] = unit["passed"]
            entry["paths"] = {}
            results.append(entry)
            continue

        now = dt.datetime.strptime(case["now"], "%Y-%m-%d").replace(tzinfo=UTC)

        paths = {}
        path_pass = {}
        for forced in ("snapshot", "live"):
            result = _run_path(case, forced, now)
            passed, checks = _evaluate(case, result)
            # The expectation generally binds ONE path; record evaluation on
            # the bound path but print both paths' raw conclusions.
            paths[forced] = {
                "result": result,
                "checks": checks,
                "bound": case.get("path", "auto") in (forced, "auto"),
            }
            path_pass[forced] = passed

        bound = case.get("path", "auto")
        if bound == "snapshot":
            overall = path_pass["snapshot"]
        elif bound == "live":
            overall = path_pass["live"]
        else:
            # Auto cases: the auto-decided path must satisfy the expectation;
            # verify against the forced path that the decision selects.
            auto = _run_path(case, None, now)
            chosen = auto.get("source", "live")
            auto_pass, auto_checks = _evaluate(case, auto)
            paths["auto"] = {
                "result": auto,
                "checks": auto_checks,
                "bound": True,
            }
            overall = auto_pass
            paths[chosen]["bound"] = True

        entry["paths"] = paths
        entry["passed"] = bool(overall)
        results.append(entry)

    return results


# ---------------------------------------------------------------------------
# Console printing
# ---------------------------------------------------------------------------

def print_results(results):
    print("=" * 78)
    print("cryptocmd specification replay  (SPEC v{0})".format(spec.SPEC_VERSION))
    print("=" * 78)
    for entry in results:
        mark = "PASS" if entry["passed"] else "FAIL"
        print(
            "[{0}] {1}  ({2} via {3})".format(
                mark, entry["id"], entry["clause"], entry["func"]
            )
        )
        print("      {0}".format(entry["title"]))
        if entry.get("paths"):
            for name in ("snapshot", "auto", "live"):
                path = entry["paths"].get(name)
                if not path:
                    continue
                res = path["result"]
                if "error" in res:
                    conclusion = "ERROR {0}: {1}".format(
                        res["error"], res["message"]
                    )
                else:
                    conclusion = (
                        "source={source} degraded={degraded} "
                        "span={span_days}d age={age_days} "
                        "votes=({span_votes},{age_votes}) fills={fills} "
                        "dropped={dropped} rows={nrows}"
                    ).format(
                        source=res.get("source"),
                        degraded=res.get("degraded"),
                        span_days=res.get("span_days"),
                        age_days=res.get("age_days"),
                        span_votes=res.get("span_votes"),
                        age_votes=res.get("age_votes"),
                        fills=res.get("fills"),
                        dropped=res.get("dropped"),
                        nrows=len(res.get("rows", [])),
                    )
                bound = " *bound*" if path.get("bound") else ""
                print("        - {0}:{1} {2}".format(name, bound, conclusion))
                for check in path.get("checks", []):
                    print("            check: {0}".format(check))
        if "checks" in entry:
            for name, ok in entry["checks"].items():
                print("            check: {0} -> {1}".format(name, ok))
        if entry.get("unit"):
            for row in entry["unit"]["rows"]:
                print(
            "            column: {given!r} -> {header!r} [{tag}] {ok}".format(
                        **row
                    )
                )
    total = len(results)
    failed = sum(1 for r in results if not r["passed"])
    print("=" * 78)
    print("{0}/{1} cases passed, {2} failed".format(total - failed, total, failed))
    print("=" * 78)
    return failed


# ---------------------------------------------------------------------------
# HTML comparison page
# ---------------------------------------------------------------------------

FUNCTION_LINKS = {
    "cryptocmd.spec.decide_source": ("cryptocmd/spec.py", "decide_source"),
    "cryptocmd.spec.snapshot_covers": ("cryptocmd/spec.py", "snapshot_covers"),
    "cryptocmd.spec.parse_quote_row": ("cryptocmd/spec.py", "parse_quote_row"),
    "cryptocmd.spec.normalize_coin": ("cryptocmd/spec.py", "normalize_coin"),
    "cryptocmd.spec.normalize_column_name": (
        "cryptocmd/spec.py",
        "normalize_column_name",
    ),
    "cryptocmd.spec.normalize_record": (
        "cryptocmd/spec.py",
        "normalize_record",
    ),
    "cryptocmd.spec.build_window": ("cryptocmd/spec.py", "build_window"),
    "cryptocmd.spec.normalize_epoch": ("cryptocmd/spec.py", "normalize_epoch"),
    "cryptocmd.spec.resolve_fiat": ("cryptocmd/spec.py", "resolve_fiat"),
    "cryptocmd.core.CryptoData": ("cryptocmd/core.py", "CryptoData"),
}


def _line_of(rel_path, symbol):
    """Best-effort source line for a symbol, to make links open at the code."""
    full = os.path.join(ROOT, rel_path.replace("/", os.sep))
    try:
        with open(full, encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, start=1):
                stripped = line.lstrip()
                if stripped.startswith("def {0}".format(symbol)) or (
                    symbol == "CryptoData"
                    and stripped.startswith("class CryptoData")
                ):
                    return lineno
    except OSError:
        pass
    return 1


def _function_anchor(func):
    rel, symbol = FUNCTION_LINKS.get(func, ("cryptocmd/spec.py", func))
    line = _line_of(rel, symbol)
    return '<a class="fn" href="../{0}#L{1}" target="_blank">{2}()</a>'.format(
        rel, line, html.escape(symbol)
    )


TAG_CLASS = {
    "snapshot": "tag-snap",
    "snapshot.default": "tag-fill",
    "live.capture": "tag-live",
    "fallback.identity": "tag-fallback",
    "fallback.literal": "tag-fallback",
}


def _result_block(name, path):
    res = path["result"]
    if "error" in res:
        body = "<p class='err'>{0}: {1}</p>".format(
            html.escape(res["error"]), html.escape(res["message"])
        )
    else:
        rows_html = [
            ("<table class='data'><tr><th>Date</th><th>Close</th>"
             "<th>Volume</th><th>cell provenance</th></tr>")
        ]
        for row in res.get("rows", []):
            tags = " ".join(
                "<span class='tag {0}'>{1}</span>".format(
                    TAG_CLASS.get(tag, ""), html.escape(tag)
                )
                for tag in sorted(set(row["tags"]))
            )
            rows_html.append(
                "<tr><td>{0}</td><td>{1}</td><td>{2}</td><td>{3}</td></tr>".format(
                    html.escape(str(row["date"])),
                    html.escape(str(row["close"])),
                    html.escape(str(row["volume"])),
                    tags,
                )
            )
        rows_html.append("</table>")
        body = (
            "<p class='meta'>source=<b>{source}</b> "
            "degraded=<b>{degraded}</b> reason={reason}<br>"
            "span={span_days}d age={age_days} votes=({span_votes},"
            "{age_votes}) covered={covered} fills={fills} dropped={dropped}"
            "</p>{rows}"
        ).format(
            source=html.escape(str(res.get("source"))),
            degraded=html.escape(str(res.get("degraded"))),
            reason=html.escape(str(res.get("reason"))),
            span_days=res.get("span_days"),
            age_days=html.escape(str(res.get("age_days"))),
            span_votes=html.escape(str(res.get("span_votes"))),
            age_votes=html.escape(str(res.get("age_votes"))),
            covered=html.escape(str(res.get("covered"))),
            fills=res.get("fills"),
            dropped=res.get("dropped"),
            rows="".join(rows_html),
        )
    checks = "".join(
        "<li class='{0}'>{1}</li>".format(
            "ok" if "-> True" in c or c.endswith("= True") else "bad",
            html.escape(c),
        )
        for c in path.get("checks", [])
    )
    bound = " <span class='bound'>bound</span>" if path.get("bound") else ""
    return (
        "<div class='path'><h4>{0}{1}</h4>{2}"
        "<ul class='checks'>{3}</ul></div>"
    ).format(html.escape(name), bound, body, checks)


def render_html(results):
    cards = []
    for entry in results:
        status = "pass" if entry["passed"] else "fail"
        clause_id = entry["clause"]
        clause_text = spec.CLAUSES.get(clause_id, "")
        header = (
            "<div class='card-head {0}'><span class='badge {0}'>{1}</span>"
            "<span class='cid'>{2}</span> {3}</div>"
        ).format(
            status,
            "PASS" if entry["passed"] else "FAIL",
            html.escape(entry["id"]),
            html.escape(entry["title"]),
        )
        basis = (
            "<p class='basis'><b>Basis:</b> <a id='{0}' class='clause'>{0}</a> "
            "{1}<br><b>Implemented by:</b> {2}</p>"
        ).format(
            html.escape(clause_id),
            html.escape(clause_text),
            _function_anchor(entry["func"]),
        )
        body = ""
        if entry.get("paths"):
            for name in ("snapshot", "auto", "live"):
                if name in entry["paths"]:
                    body += _result_block(name, entry["paths"][name])
        if entry.get("unit"):
            body += "<table class='data'><tr><th>Given</th><th>Canonical</th>"
            body += "<th>Tag</th><th>OK</th></tr>"
            for row in entry["unit"]["rows"]:
                body += "<tr><td>{given}</td><td>{header}</td>"
                body += "<td><span class='tag {cls}'>{tag}</span></td>"
                body += "<td>{ok}</td></tr>"
                body = body.format(
                    given=html.escape(row["given"]),
                    header=html.escape(str(row["header"])),
                    cls=TAG_CLASS.get(row["tag"], ""),
                    tag=html.escape(row["tag"]),
                    ok=row["ok"],
                )
            body += "</table>"
        if "checks" in entry:
            body += "<ul class='checks'>"
            for name, ok in entry["checks"].items():
                body += "<li class='{0}'>{1} -> {2}</li>".format(
                    "ok" if ok else "bad", html.escape(name), ok
                )
            body += "</ul>"
        cards.append(
            "<div class='card' id='card-{0}'>{1}{2}{3}</div>".format(
                html.escape(entry["id"]), header, basis, body
            )
        )

    clause_index = "".join(
        "<li><a class='clause' id='{0}'>{0}</a> - {1}</li>".format(
            html.escape(cid), html.escape(text)
        )
        for cid, text in spec.CLAUSES.items()
    )

    return """<!doctype html>
<html><head><meta charset="utf-8"><title>cryptocmd spec replay</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1b2330;background:#f6f7fb}}
h1{{font-size:20px}} .card{{background:#fff;border:1px solid #e3e6ef;border-radius:8px;
padding:14px 16px;margin:14px 0;box-shadow:0 1px 2px rgba(0,0,0,.04)}}
.card-head{{font-weight:600;margin-bottom:6px}}
.badge{{padding:2px 8px;border-radius:10px;color:#fff;font-size:12px;margin-right:8px}}
.badge.pass{{background:#1a7f37}} .badge.fail{{background:#c0392b}}
.cid{{color:#5b6472;font-family:Consolas,monospace;margin-right:8px}}
.basis{{background:#f1f4fb;border-left:3px solid #4466cc;padding:8px 10px;font-size:13px}}
a.fn{{color:#0b5cad;text-decoration:none;font-family:Consolas,monospace}}
a.fn:hover{{text-decoration:underline}}
a.clause{{color:#7a3fb0;font-family:Consolas,monospace;font-weight:700}}
.path{{border-top:1px dashed #d8dce8;margin-top:8px;padding-top:6px}}
.path h4{{margin:4px 0;font-size:14px}}
.meta{{font-size:12.5px;color:#39414f}} .err{{color:#c0392b;font-weight:600}}
table.data{{border-collapse:collapse;margin:6px 0;font-size:12.5px}}
table.data th,table.data td{{border:1px solid #dfe3ee;padding:3px 8px;text-align:left}}
.tag{{display:inline-block;padding:1px 6px;border-radius:8px;font-size:11px;margin:1px}}
.tag-snap{{background:#e7e0ff;color:#4b2fa0}}
.tag-fill{{background:#ffe7c2;color:#9a5a00}}
.tag-live{{background:#dcf3e3;color:#176630}}
.tag-fallback{{background:#fbdada;color:#9a2626}}
ul.checks{{font-family:Consolas,monospace;font-size:12px;margin:4px 0;padding-left:18px}}
li.ok{{color:#1a7f37}} li.bad{{color:#c0392b}}
.bound{{background:#4466cc;color:#fff;border-radius:6px;padding:0 5px;font-size:10px}}
#toc{{background:#fff;border:1px solid #e3e6ef;border-radius:8px;padding:10px 16px}}
#toc li{{margin:3px 0;font-size:13px}}
</style></head><body>
<h1>cryptocmd specification replay &mdash; SPEC v{version}</h1>
<p>Each card shows the <b>snapshot</b> and <b>live</b> path conclusions. Click the
purple clause id for the rule text and the blue function link to open the exact
implementing source line.</p>
<div id="toc"><b>Clause index</b><ul>{clause_index}</ul></div>
{cards}
</body></html>""".format(
        version=html.escape(spec.SPEC_VERSION),
        clause_index=clause_index,
        cards="".join(cards),
    )


# ---------------------------------------------------------------------------
# Baseline + CLI
# ---------------------------------------------------------------------------

def _serializable(results):
    """Stable, baseline-comparable projection of the conclusions."""
    payload = {"spec_version": spec.SPEC_VERSION, "cases": []}
    for entry in results:
        case = {
            "id": entry["id"],
            "clause": entry["clause"],
            "passed": entry["passed"],
        }
        if entry.get("paths"):
            case["paths"] = {}
            for name, path in entry["paths"].items():
                res = path["result"]
                compact = {
                    k: res.get(k)
                    for k in (
                        "error",
                        "source",
                        "degraded",
                        "reason",
                        "span_days",
                        "covered",
                        "span_votes",
                        "age_votes",
                        "fills",
                        "dropped",
                    )
                }
                compact["rows"] = [
                    [r["date"], r["close"], r["volume"]]
                    for r in res.get("rows", [])
                ]
                if res.get("identity"):
                    compact["identity"] = {
                        "canonical_code": res["identity"].get("canonical_code"),
                        "source": res["identity"].get("source"),
                        "alias": res["identity"].get("alias"),
                    }
                if res.get("fiat"):
                    compact["fiat"] = {
                        "code": res["fiat"].get("code"),
                        "source": res["fiat"].get("source"),
                    }
                case["paths"][name] = compact
        if entry.get("unit"):
            case["unit"] = entry["unit"]["rows"]
        if "checks" in entry:
            case["checks"] = entry["checks"]
        payload["cases"].append(case)
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description="cryptocmd spec replay")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail on any case mismatch and compare with the baseline",
    )
    parser.add_argument(
        "--regen", action="store_true", help="rewrite the committed baseline"
    )
    args = parser.parse_args(argv)

    os.makedirs(OUT_DIR, exist_ok=True)
    results = run_cases()
    failed = print_results(results)

    with open(RESULT_PATH, "w", encoding="utf-8") as handle:
        json.dump(_serializable(results), handle, indent=2, sort_keys=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as handle:
        handle.write(render_html(results))

    print("\nReport written to: {0}".format(REPORT_PATH))
    print("Results written to: {0}".format(RESULT_PATH))

    payload = _serializable(results)
    canonical = json.dumps(payload, indent=2, sort_keys=True)

    if args.regen:
        with open(BASELINE_PATH, "w", encoding="utf-8") as handle:
            handle.write(canonical)
        print("Baseline regenerated: {0}".format(BASELINE_PATH))
        return 0

    if args.strict:
        if not os.path.exists(BASELINE_PATH):
            print("STRICT FAIL: baseline missing; run with --regen first.")
            return 2
        with open(BASELINE_PATH, encoding="utf-8") as handle:
            baseline = handle.read()
        if baseline != canonical:
            print("STRICT FAIL: conclusions differ from baseline.")
            print("Inspect {0} or regenerate with --regen.".format(REPORT_PATH))
            return 3
        if failed:
            print("STRICT FAIL: {0} case(s) failed expectations.".format(failed))
            return 4
        print("STRICT OK: all cases pass and match the baseline.")

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
