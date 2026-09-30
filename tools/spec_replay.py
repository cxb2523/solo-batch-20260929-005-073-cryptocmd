#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import hashlib
import html
import inspect
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cryptocmd.core import CryptoData, validate_crypto_data_signature
from cryptocmd.spec import (
    InvalidSpecTimestamp,
    InvalidSpecWindow,
    canonical_headers,
    get_rule,
    iter_upstream_windows,
    normalize_column_headers,
    parse_iso_utc,
    parse_quote_table,
    reconcile_sources,
    resolve_coin,
    resolve_fiat,
    rule_basis,
    validate_request_window,
    validate_unix_timestamp,
)

FIXTURE_DIR = ROOT / "tools" / "spec_fixtures"
BASELINE_PATH = ROOT / "tools" / "spec_replay_baseline.json"
REPORT_JSON_PATH = ROOT / "tools" / "spec_replay_report.json"
REPORT_HTML_PATH = ROOT / "tools" / "spec_replay_report.html"

FUNCTIONS = {
    "resolve_coin": resolve_coin,
    "resolve_fiat": resolve_fiat,
    "validate_request_window": validate_request_window,
    "reconcile_sources": reconcile_sources,
    "normalize_column_headers": normalize_column_headers,
    "parse_quote_table": parse_quote_table,
    "validate_crypto_data_signature": validate_crypto_data_signature,
    "validate_unix_timestamp": validate_unix_timestamp,
    "iter_upstream_windows": iter_upstream_windows,
}


def _stable(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _table_digest(response: Dict[str, Any], source: str) -> Dict[str, Any]:
    table = parse_quote_table(response, source)
    return {
        "headers": list(table.headers),
        "rows": [list(row) for row in table.rows],
        "markers": list(table.markers),
        "field_sources": {key: list(values) for key, values in table.field_sources.items()},
        "basis": table.basis,
    }


def _replay_case(payload: Dict[str, Any]) -> Dict[str, Any]:
    request = payload["request"]
    now = parse_iso_utc(payload["now"])
    conclusions: List[Dict[str, Any]] = []
    errors: List[str] = []
    result: Dict[str, Any] = {
        "case_id": payload["case_id"],
        "conclusions": conclusions,
        "errors": errors,
    }

    try:
        coin = resolve_coin(
            request.get("coin_code"), request.get("coin_name"), request.get("id_number")
        )
        conclusions.append({
            "path": "coin_alias",
            "function": "resolve_coin",
            "ok": True,
            "result": {
                "symbol": coin.canonical_symbol,
                "name": coin.canonical_name,
                "id": coin.coin_id,
                "aliases": list(coin.aliases),
                "source": coin.source,
            },
            "basis": [coin.basis, rule_basis("normalization.order")],
        })
    except Exception as exc:
        errors.append(type(exc).__name__ + ": " + str(exc))

    try:
        fiat = resolve_fiat(request["fiat"])
        conclusions.append({
            "path": "fiat_id",
            "function": "resolve_fiat",
            "ok": True,
            "result": {"code": fiat.code, "id": fiat.convert_id, "source": fiat.source},
            "basis": [fiat.basis, rule_basis("fiat_ids.priority")],
        })
    except Exception as exc:
        errors.append(type(exc).__name__ + ": " + str(exc))

    try:
        window = validate_request_window(request["start"], request["end"])
        conclusions.append({
            "path": "request_window",
            "function": "validate_request_window",
            "ok": True,
            "result": {
                "start": request["start"],
                "end": request["end"],
                "span_days": window.span_days,
                "time_start": window.time_start,
                "time_end": window.time_end,
                "pages": len(iter_upstream_windows(window)),
            },
            "basis": [window.basis, rule_basis("windows.same_day_span_is_valid")],
        })
    except InvalidSpecWindow as exc:
        conclusions.append({
            "path": "request_window",
            "function": "validate_request_window",
            "ok": False,
            "result": {"error": type(exc).__name__},
            "basis": [str(exc)],
        })
        result["expected"] = payload.get("expected", {})
        result["fingerprint"] = _stable({"case": payload["case_id"], "error": type(exc).__name__})
        return result

    try:
        timestamp_unit = validate_unix_timestamp(window.time_start)
        conclusions.append({
            "path": "timestamp_unit",
            "function": "validate_unix_timestamp",
            "ok": True,
            "result": {"seconds": timestamp_unit},
            "basis": [rule_basis("time.timestamp_unit"), rule_basis("time.timestamp_kind")],
        })
    except InvalidSpecTimestamp as exc:
        errors.append(type(exc).__name__ + ": " + str(exc))

    for path_name, response_payload, source_marker in (
        ("snapshot_path", payload.get("snapshot"), get_rule("source_policy.snapshot_marker")),
        ("live_path", payload.get("live"), get_rule("source_policy.live_marker")),
    ):
        if response_payload is None:
            continue
        try:
            path_table = _table_digest(response_payload, source_marker)
            conclusions.append({
                "path": path_name,
                "function": "parse_quote_table",
                "ok": True,
                "result": {
                    "source": source_marker,
                    "headers": path_table["headers"],
                    "rows": path_table["rows"],
                    "markers": path_table["markers"],
                    "field_sources": path_table["field_sources"],
                },
                "basis": [path_table["basis"], "spec:source_policy.{}={!r}".format("snapshot_marker" if path_name == "snapshot_path" else "live_marker", source_marker)],
            })
        except Exception as exc:
            conclusions.append({
                "path": path_name,
                "function": "parse_quote_table",
                "ok": False,
                "result": {"error": type(exc).__name__, "message": str(exc)},
                "basis": [rule_basis("table.missing_required_policy")],
            })

    if payload.get("snapshot") is not None or payload.get("live") is not None:
        try:
            decision = reconcile_sources(
                payload.get("snapshot"), payload.get("live"), window, now,
                live_failure=bool(request.get("live_failure")),
            )
            table = _table_digest(decision.selected_response, decision.selected_source)
            conclusions.append({
                "path": "snapshot_vs_live",
                "function": "reconcile_sources",
                "ok": True,
                "result": {
                    "source": decision.selected_source,
                    "markers": list(decision.markers),
                    "conflicts": list(decision.conflicts),
                    "default_records": list(decision.default_records),
                    "row_count": len(table["rows"]),
                    "rows": table["rows"],
                },
                "basis": list(decision.basis),
            })
        except Exception as exc:
            conclusions.append({
                "path": "snapshot_vs_live",
                "function": "reconcile_sources",
                "ok": False,
                "result": {"error": type(exc).__name__, "message": str(exc)},
                "basis": [rule_basis("source_policy.complete_coverage_required")],
            })

    try:
        normalized, sources, basis = normalize_column_headers(payload["input_headers"])
        conclusions.append({
            "path": "column_name",
            "function": "normalize_column_headers",
            "ok": normalized == canonical_headers(),
            "result": {"headers": list(normalized), "sources": list(sources)},
            "basis": [basis, rule_basis("normalization.fallback_order")],
        })
    except Exception as exc:
        errors.append(type(exc).__name__ + ": " + str(exc))

    validate_crypto_data_signature()
    sample = CryptoData(
        "BTC", "01-01-2024", "02-01-2024", False, False, "USD",
        "Bitcoin", 1, list(canonical_headers()), [], aliases=("btc", "bitcoin"),
    )
    conclusions.append({
        "path": "crypto_data_compatibility",
        "function": "validate_crypto_data_signature",
        "ok": sample.aliases == ("btc", "bitcoin") and len(sample.headers) == 11,
        "result": {"aliases": list(sample.aliases), "legacy_positional_fields": 10},
        "basis": [rule_basis("compatibility.crypto_data_legacy_signature"), rule_basis("compatibility.new_kw_only_fields")],
    })

    public_rows = None
    for conclusion in conclusions:
        if conclusion["path"] == "snapshot_vs_live" and conclusion["ok"]:
            public_rows = conclusion["result"]["rows"]
    result["fingerprint"] = _stable({
        "case": payload["case_id"],
        "expected": payload.get("expected", {}),
        "conclusions": [
            {
                "path": item["path"],
                "ok": item["ok"],
                "result": item["result"],
                "basis": item["basis"],
            }
            for item in conclusions
        ],
        "public_rows": public_rows,
        "errors": errors,
    })
    result["expected"] = payload.get("expected", {})
    return result


def _expected_ok(case: Dict[str, Any]) -> bool:
    expected = case.get("expected", {})
    if "error" in expected:
        return any(item.get("result", {}).get("error") == expected["error"] for item in case["conclusions"])
    for item in case["conclusions"]:
        if not item["ok"]:
            return False
    if expected:
        for key, value in expected.items():
            source_item = next((item for item in case["conclusions"] if item["path"] == "snapshot_vs_live"), None)
            column_item = next((item for item in case["conclusions"] if item["path"] == "column_name"), None)
            coin_item = next((item for item in case["conclusions"] if item["path"] == "coin_alias"), None)
            fiat_item = next((item for item in case["conclusions"] if item["path"] == "fiat_id"), None)
            if key in {"source", "markers", "conflict_count", "default_count", "row_count"} and source_item:
                actual = source_item["result"]
                if key == "source" and actual["source"] != value:
                    return False
                if key == "markers" and actual["markers"] != value:
                    return False
                if key == "conflict_count" and len(actual["conflicts"]) != value:
                    return False
                if key == "default_count" and len(actual["default_records"]) != value:
                    return False
                if key == "row_count" and actual["row_count"] != value:
                    return False
            elif key in {"coin_id"} and coin_item and coin_item["result"]["id"] != value:
                return False
            elif key in {"fiat_id"} and fiat_item and fiat_item["result"]["id"] != value:
                return False
            elif key == "first_header_sources" and column_item and column_item["result"]["sources"][:len(value)] != value:
                return False
    return True


def write_html(cases: List[Dict[str, Any]], strict: bool, out_path: Path) -> None:
    cards = []
    for name, function in FUNCTIONS.items():
        source = html.escape(inspect.getsource(function))
        cards.append('<details id="fn-{0}"><summary>{0}</summary><pre>{1}</pre></details>'.format(html.escape(name), source))
    sections = []
    for case in cases:
        status = "PASS" if _expected_ok(case) else "FAIL"
        rows = []
        for item in case["conclusions"]:
            basis = "<br>".join('<a href="#fn-{0}">{0}</a> <code>{1}</code>'.format(html.escape(item["function"]), html.escape(str(part))) for part in item["basis"])
            rows.append(
                "<tr><td>{0}</td><td>{1}</td><td><pre>{2}</pre></td><td>{3}</td></tr>".format(
                    html.escape(item["path"]),
                    "PASS" if item["ok"] else "FAIL",
                    html.escape(json.dumps(item["result"], ensure_ascii=False, indent=2)),
                    basis,
                )
            )
        sections.append(
            '<section><h2 id="case-{0}">{0} <span>{1}</span></h2><table><thead><tr><th>路径</th><th>结论</th><th>取值</th><th>函数与依据</th></tr></thead><tbody>{2}</tbody></table></section>'.format(
                html.escape(case["case_id"]), status, "".join(rows)
            )
        )
    document = (
        "<!doctype html><html><head><meta charset=""utf-8""><title>spec replay</title>"
        + "<style>body{font:14px/1.5 system-ui,sans-serif;margin:2rem}"
        + "details{margin:1rem 0;border:1px solid #ccc;padding:1rem}"
        + "pre{white-space:pre-wrap;overflow-wrap:anywhere}"
        + "table{border-collapse:collapse;width:100%;margin:1rem 0}"
        + "td,th{border:1px solid #ccc;padding:.5rem;vertical-align:top}"
        + "h2 span{font-size:.8rem;color:#fff;background:#070;padding:.2rem .4rem;border-radius:4px}"
        + ".toc li{margin:.25rem}</style></head><body>"
        + "<h1>cryptocmd 规格回放对照</h1><p>strict={0}</p>".format(html.escape(str(strict)))
        + "<nav class=""toc""><ul>{0}</ul></nav>".format("".join("<li><a href=""#case-{0}"">{0}</a></li>".format(html.escape(case["case_id"])) for case in cases))
        + "".join(sections)
        + "<h2>函数依据</h2>" + "".join(cards)
        + "</body></html>"
    )
    out_path.write_text(document, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args()

    cases = []
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        case = _replay_case(payload)
        cases.append(case)
        print("[{0}] {1}".format("PASS" if _expected_ok(case) else "FAIL", case["case_id"]))
        for item in case["conclusions"]:
            print("  - {0}: {1} via {2}".format(item["path"], "PASS" if item["ok"] else "FAIL", item["function"]))
            print("    {0}".format("; ".join(item["basis"])))
        for error in case["errors"]:
            print("  - error: {0}".format(error))

    report = {"strict": args.strict, "cases": cases}
    REPORT_JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_html(cases, args.strict, REPORT_HTML_PATH)

    baseline = {"cases": [{"case_id": case["case_id"], "fingerprint": case["fingerprint"]} for case in cases]}
    if args.update_baseline or not BASELINE_PATH.exists():
        BASELINE_PATH.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
        print("baseline updated: {0}".format(BASELINE_PATH))

    expected_failures = [case["case_id"] for case in cases if not _expected_ok(case)]
    existing = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    baseline_changed = existing != baseline
    if expected_failures or (args.strict and baseline_changed):
        print("strict replay failed", file=sys.stderr)
        return 1
    print("report: {0}".format(REPORT_HTML_PATH))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


