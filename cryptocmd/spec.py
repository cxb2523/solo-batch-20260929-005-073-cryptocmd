#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple


SPEC_DIR = os.path.dirname(__file__)
BUILTIN_SNAPSHOT_PATH = os.path.join(SPEC_DIR, "builtin_snapshot.json")


SPEC: Dict[str, Any] = {
    "meta": {
        "version": 1,
        "name": "cryptocmd executable value specification",
        "description": "Canonical coins, aliases, fiat identifiers, windows, source reconciliation, and table parsing.",
    },
    "time": {
        "input_date_format": "%d-%m-%Y",
        "input_timezone": "UTC",
        "timestamp_unit": "second",
        "timestamp_kind": "unix_integer",
        "millisecond_threshold_abs": 10_000_000_000,
        "iso_timezone": "UTC",
        "iso_allowed_suffix": "Z",
        "date_output_format": "%d-%m-%Y",
        "default_all_time_start": "28-04-2013",
        "default_end": "today_utc_midnight",
    },
    "request": {
        "api_base_url": "https://api.coinmarketcap.com/data-api/v3",
        "http_headers": {"User-Agent": "Mozilla/5.0"},
        "listing_path": "/cryptocurrency/listing",
        "historical_path": "/cryptocurrency/historical",
        "listing_query": "start=1&limit=5000&sortBy=market_cap&sortType=desc&convert={convert}&cryptoType=all&tagType=all&audited=false",
        "historical_query": "id={id}&convertId={convert_id}&timeStart={time_start}&timeEnd={time_end}",
        "success_error_codes": [0, "0"],
        "not_found_error_codes": [400, "400"],
        "listing_response_path": "data.cryptoCurrencyList",
        "coin_id_field": "id",
        "coin_symbol_field": "symbol",
        "coin_name_field": "name",
        "status_error_code_path": ["status", "error_code"],
        "status_error_message_path": ["status", "error_message"],
    },
    "windows": {
        "public_interval": "daily_candle[start_utc_midnight,end_utc_midnight]",
        "public_start_boundary": "closed",
        "public_end_boundary": "closed",
        "same_day_span_days": 0,
        "same_day_span_is_valid": True,
        "negative_span_is_valid": False,
        "upstream_time_start_boundary": "exclusive",
        "upstream_time_end_boundary": "inclusive",
        "requested_start_to_upstream_start_offset_days": -1,
        "upstream_timestamp_unit": "second",
        "page_size_days": 365,
        "chunk_advance_days_after_end": 0,
        "upstream_quotes_path": "data.quotes",
        "quote_date_field": "timeOpen",
        "row_order": "descending_by_quote_date",
    },
    "source_policy": {
        "snapshot_preferred_max_request_span_days": 1,
        "snapshot_fresh_max_age_days": 7,
        "future_snapshot_age_is_valid": False,
        "complete_coverage_required": True,
        "conflict_winner": "snapshot",
        "degraded_marker_on_conflict": "snapshot_conflict",
        "snapshot_defaults_marker": "snapshot_defaulted_fields",
        "snapshot_marker": "builtin_snapshot",
        "live_marker": "live_response",
        "live_missing_marker": "live_missing_fields",
        "fallback_marker": "snapshot_live_failure_fallback",
        "stale_fallback_marker": "snapshot_stale_fallback",
        "decision_order": [
            "coverage_evaluated",
            "request_span_evaluated",
            "snapshot_age_evaluated",
            "conflict_always_won_by_snapshot",
        ],
        "snapshot_generated_at_path": ["generated_at"],
        "snapshot_data_path": ["data"],
    },
    "normalization": {
        "order": ["coin_alias", "column_name"],
        "fallback_order": ["mapped_path", "positional_header", "reject"],
        "case": "case_insensitive_exact_after_trim",
    },
    "fiat_ids": {
        "priority": ["exact_input_case_insensitive", "declared_alias_case_insensitive"],
        "default_listing_convert_code": "USD",
        "ids": {
            "AED": 2813, "ARS": 2821, "AUD": 2782, "BDT": 3530,
            "BGN": 2814, "BRL": 2783, "CAD": 2784, "CHF": 2785,
            "CLP": 2786, "CNY": 2787, "COP": 2820, "CZK": 2788,
            "DKK": 2789, "EGP": 3538, "EUR": 2790, "GBP": 2791,
            "GHS": 3540, "HKD": 2792, "HUF": 2793, "IDR": 2794,
            "ILS": 2795, "INR": 2796, "IRR": 3544, "JPY": 2797,
            "KRW": 2798, "MAD": 3554, "MXN": 2799, "MYR": 2800,
            "NGN": 2819, "NOK": 2801, "NZD": 2802, "PHP": 2803,
            "PKR": 2804, "PLN": 2805, "RON": 2817, "RUB": 2806,
            "SAR": 3566, "SEK": 2807, "SGD": 2808, "THB": 2809,
            "TRY": 2810, "TWD": 2811, "UAH": 2824, "USD": 2781,
            "VES": 3573, "VND": 2823, "ZAR": 2812,
        },
        "aliases": {
            "US$": "USD", "USDOLLAR": "USD", "DOLLAR": "USD",
            "€": "EUR", "EURO": "EUR", "£": "GBP", "POUND": "GBP",
            "RMB": "CNY", "YUAN": "CNY",
        },
    },
    "coins": {
        "canonical": [
            {"id": 1, "symbol": "BTC", "name": "Bitcoin", "aliases": ["bitcoin", "btc", "xbt", "bit coin"]},
            {"id": 1027, "symbol": "ETH", "name": "Ethereum", "aliases": ["ethereum", "eth", "ether"]},
            {"id": 5426, "symbol": "SOL", "name": "Solana", "aliases": ["solana", "sol", "sol-solana"]},
            {"id": 15465, "symbol": "SOL", "name": "Solcoin", "aliases": ["solcoin", "sol-solcoin"]},
        ],
        "unknown_symbol_source": "fallback_live_listing_by_symbol",
        "unknown_name_source": "fallback_live_listing_by_symbol_and_name",
        "ambiguous_without_name_source": "reject_ambiguous_symbol",
    },
    "table": {
        "canonical_columns": [
            {"key": "date", "header": "Date", "kind": "date", "required": True, "paths": [["timeOpen"]]},
            {"key": "open", "header": "Open", "kind": "decimal", "required": False, "paths": [["quote", "open"], ["open"]]},
            {"key": "high", "header": "High", "kind": "decimal", "required": False, "paths": [["quote", "high"], ["high"]]},
            {"key": "low", "header": "Low", "kind": "decimal", "required": False, "paths": [["quote", "low"], ["low"]]},
            {"key": "close", "header": "Close", "kind": "decimal", "required": False, "paths": [["quote", "close"], ["close"]]},
            {"key": "volume", "header": "Volume", "kind": "decimal", "required": False, "paths": [["quote", "volume"], ["volume"]]},
            {"key": "market_cap", "header": "Market Cap", "kind": "decimal", "required": False, "paths": [["quote", "marketCap"], ["marketCap"], ["market_cap"]]},
            {"key": "time_open", "header": "Time Open", "kind": "timestamp", "required": False, "paths": [["timeOpen"], ["time_open"]]},
            {"key": "time_high", "header": "Time High", "kind": "timestamp", "required": False, "paths": [["timeHigh"], ["time_high"]]},
            {"key": "time_low", "header": "Time Low", "kind": "timestamp", "required": False, "paths": [["timeLow"], ["time_low"]]},
            {"key": "time_close", "header": "Time Close", "kind": "timestamp", "required": False, "paths": [["timeClose"], ["time_close"]]},
        ],
        "input_column_aliases": {
            "date": ["date", "day"],
            "open": ["open", "open price", "price open"],
            "high": ["high", "high price", "price high"],
            "low": ["low", "low price", "price low"],
            "close": ["close", "closing price", "price close"],
            "volume": ["volume", "vol", "trading volume"],
            "market_cap": ["market cap", "marketcap", "market_cap", "mcap", "cap"],
            "time_open": ["time open", "timeopen", "time_open"],
            "time_high": ["time high", "timehigh", "time_high"],
            "time_low": ["time low", "timelow", "time_low"],
            "time_close": ["time close", "timeclose", "time_close"],
        },
        "default_values": {"decimal": None, "timestamp": None},
        "missing_required_policy": "reject",
        "unknown_columns_positional_fallback": True,
        "provenance_sources": {
            "canonical": "spec_column_path",
            "alias": "spec_column_alias",
            "positional": "spec_column_positional_fallback",
            "default": "spec_snapshot_default",
            "missing": "live_missing_field",
        },
    },
    "compatibility": {
        "crypto_data_legacy_signature": "coin_code,start_date,end_date,all_time,order_ascending,fiat,coin_name,id_number,headers,rows",
        "new_kw_only_fields": ["aliases"],
        "legacy_positional_arity": 10,
        "maximum_positional_arity": 10,
        "legacy_fields_must_remain": ["coin_code", "start_date", "end_date", "all_time", "order_ascending", "fiat", "coin_name", "id_number", "headers", "rows"],
    },
}


class SpecIntegrityError(RuntimeError):
    """The executable specification itself is invalid."""


class InvalidSpecWindow(ValueError):
    """A request violates the window semantics declared by the spec."""


class InvalidSpecTimestamp(ValueError):
    """A timestamp does not use the unit and kind declared by the spec."""


class InvalidSpecResponse(ValueError):
    """A response cannot satisfy the table parsing specification."""


class AmbiguousCoinIdentity(ValueError):
    """A symbol maps to multiple canonical coins without a disambiguating name."""


@dataclass(frozen=True)
class CoinIdentity:
    canonical_symbol: Optional[str]
    canonical_name: Optional[str]
    coin_id: Optional[int]
    aliases: Tuple[str, ...]
    source: str
    basis: str


@dataclass(frozen=True)
class FiatIdentity:
    code: str
    convert_id: int
    source: str
    basis: str


@dataclass(frozen=True)
class RequestWindow:
    start: dt.datetime
    end: dt.datetime
    upstream_start: dt.datetime
    upstream_end: dt.datetime
    time_start: int
    time_end: int
    span_days: int
    basis: str


@dataclass(frozen=True)
class ParsedTable:
    headers: Tuple[str, ...]
    rows: Tuple[Tuple[Any, ...], ...]
    field_sources: Dict[str, Tuple[str, ...]]
    markers: Tuple[str, ...]
    basis: str


@dataclass(frozen=True)
class SourceDecision:
    selected_response: Dict[str, Any]
    selected_source: str
    markers: Tuple[str, ...]
    conflicts: Tuple[str, ...]
    default_records: Tuple[Dict[str, Any], ...]
    basis: Tuple[str, ...]


def get_rule(path: str) -> Any:
    node: Any = SPEC
    for part in path.split("."):
        node = node[part]
    return node


def rule_basis(path: str) -> str:
    return "spec:{}={!r}".format(path, get_rule(path))


def get_path(payload: Any, path: Sequence[str]) -> Any:
    current = payload
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _utc_midnight(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time.min, tzinfo=dt.timezone.utc)


def utc_today_midnight(now: Optional[dt.datetime] = None) -> dt.datetime:
    moment = now or dt.datetime.now(dt.timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() != dt.timedelta(0):
        raise InvalidSpecTimestamp(rule_basis("time.input_timezone"))
    return _utc_midnight(moment.date())


def parse_spec_date(value: str) -> dt.datetime:
    parsed = dt.datetime.strptime(value, get_rule("time.input_date_format"))
    return parsed.replace(tzinfo=dt.timezone.utc)


def format_spec_date(value: dt.datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != dt.timedelta(0):
        raise InvalidSpecTimestamp(rule_basis("time.iso_timezone"))
    return value.strftime(get_rule("time.date_output_format"))


def parse_iso_utc(value: Any) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith(get_rule("time.iso_allowed_suffix")):
        raise InvalidSpecTimestamp(rule_basis("time.iso_allowed_suffix"))
    text = value[:-1] + "+0000"
    parsed = dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%f%z")
    if parsed.utcoffset() != dt.timedelta(0):
        raise InvalidSpecTimestamp(rule_basis("time.iso_timezone"))
    return parsed.astimezone(dt.timezone.utc)


def normalize_key(value: Any) -> str:
    return str(value).strip().lower()


def resolve_fiat(code: Any) -> FiatIdentity:
    raw = str(code)
    key = normalize_key(raw)
    ids = get_rule("fiat_ids.ids")
    if key.upper() in ids:
        return FiatIdentity(key.upper(), ids[key.upper()], "fiat_ids.exact", rule_basis("fiat_ids.priority"))
    aliases = get_rule("fiat_ids.aliases")
    alias_keys = {normalize_key(candidate): target for candidate, target in aliases.items()}
    if key in alias_keys and alias_keys[key] in ids:
        canonical = alias_keys[key]
        return FiatIdentity(canonical, ids[canonical], "fiat_ids.aliases", rule_basis("fiat_ids.priority"))
    raise ValueError("Unknown fiat '{0}'. Supported: {1}.".format(raw, ", ".join(sorted(ids))))


def supported_fiat_codes() -> Tuple[str, ...]:
    return tuple(sorted(get_rule("fiat_ids.ids")))


def _canonical_index() -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Dict[str, Any]]]:
    by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    by_alias: Dict[str, Dict[str, Any]] = {}
    for coin in get_rule("coins.canonical"):
        symbol_key = normalize_key(coin["symbol"])
        by_symbol.setdefault(symbol_key, []).append(coin)
    for coin in get_rule("coins.canonical"):
        symbol_key = normalize_key(coin["symbol"])
        for alias in coin["aliases"]:
            alias_key = normalize_key(alias)
            if alias_key == symbol_key and len(by_symbol[symbol_key]) > 1:
                continue
            if alias_key in by_alias and by_alias[alias_key] != coin:
                raise SpecIntegrityError("duplicate coin alias: {0}".format(alias))
            by_alias[alias_key] = coin
    return by_symbol, by_alias


def _coin_identity(coin: Dict[str, Any], source: str) -> CoinIdentity:
    return CoinIdentity(
        coin["symbol"], coin["name"], int(coin["id"]),
        tuple(coin["aliases"]), source,
        rule_basis("normalization.order"),
    )


def resolve_coin(coin_code: Any = None, coin_name: Any = None, id_number: Any = None) -> CoinIdentity:
    if id_number is not None:
        return CoinIdentity(
            str(coin_code).upper() if coin_code is not None else None,
            str(coin_name) if coin_name is not None else None,
            int(id_number), (), "input.id_number",
            "spec:request.id_number=bypasses coins.aliases",
        )
    code_key = normalize_key(coin_code) if coin_code not in (None, "") else ""
    name_key = normalize_key(coin_name) if coin_name not in (None, "") else ""
    by_symbol, by_alias = _canonical_index()

    if name_key:
        coin = by_alias.get(name_key)
        if coin is not None and (not code_key or normalize_key(coin["symbol"]) == code_key):
            return _coin_identity(coin, "coins.aliases")
    if code_key:
        coin = by_alias.get(code_key)
        if coin is not None and not name_key:
            return _coin_identity(coin, "coins.aliases")
        matches = by_symbol.get(code_key, [])
        if len(matches) == 1 and not name_key:
            return _coin_identity(matches[0], "coins.canonical")
        if matches and name_key:
            for candidate in matches:
                if name_key in {normalize_key(alias) for alias in candidate["aliases"]}:
                    return _coin_identity(candidate, "coins.aliases")
        if len(matches) > 1 and not name_key:
            raise AmbiguousCoinIdentity(
                "coin_code '{0}' is ambiguous; pass coin_name; ".format(coin_code)
                + rule_basis("coins.ambiguous_without_name_source")
            )

    source = (
        get_rule("coins.unknown_name_source")
        if name_key else get_rule("coins.unknown_symbol_source")
    )
    return CoinIdentity(
        str(coin_code).upper() if coin_code is not None else None,
        str(coin_name) if coin_name is not None else None,
        None, (), source, rule_basis("normalization.fallback_order"),
    )


def validate_request_window(start_value: str, end_value: str) -> RequestWindow:
    start = parse_spec_date(start_value)
    end = parse_spec_date(end_value)
    span_days = (end.date() - start.date()).days
    if span_days < 0 and not get_rule("windows.negative_span_is_valid"):
        raise InvalidSpecWindow(
            "end_date must be on or after start_date; "
            + rule_basis("windows.negative_span_is_valid")
        )
    offset = get_rule("windows.requested_start_to_upstream_start_offset_days")
    upstream_start = start + dt.timedelta(days=offset)
    return RequestWindow(
        start=start, end=end, upstream_start=upstream_start, upstream_end=end,
        time_start=to_unix_seconds(upstream_start), time_end=to_unix_seconds(end),
        span_days=span_days,
        basis=(rule_basis("windows.upstream_time_start_boundary") + "; " + rule_basis("windows.upstream_time_end_boundary")),
    )


def to_unix_seconds(value: dt.datetime) -> int:
    if value.tzinfo is None or value.utcoffset() != dt.timedelta(0):
        raise InvalidSpecTimestamp(rule_basis("time.input_timezone"))
    return int(value.timestamp())


def validate_unix_timestamp(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidSpecTimestamp(rule_basis("time.timestamp_kind"))
    if abs(value) >= get_rule("time.millisecond_threshold_abs"):
        raise InvalidSpecTimestamp("timestamp unit must be seconds; " + rule_basis("time.timestamp_unit"))
    return value


def iter_upstream_windows(window: RequestWindow) -> Tuple[Tuple[dt.datetime, dt.datetime], ...]:
    page_size = dt.timedelta(days=get_rule("windows.page_size_days"))
    advance = dt.timedelta(days=get_rule("windows.chunk_advance_days_after_end"))
    chunks: List[Tuple[dt.datetime, dt.datetime]] = []
    cursor = window.upstream_start
    while cursor < window.upstream_end:
        chunk_end = min(cursor + page_size, window.upstream_end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + advance
    return tuple(chunks)


def canonical_headers() -> Tuple[str, ...]:
    return tuple(column["header"] for column in get_rule("table.canonical_columns"))


def _column_by_key(key: str) -> Dict[str, Any]:
    for column in get_rule("table.canonical_columns"):
        if column["key"] == key:
            return column
    raise SpecIntegrityError("unknown table column key: {0}".format(key))


def normalize_column_headers(input_headers: Sequence[Any]) -> Tuple[Tuple[str, ...], Tuple[str, ...], str]:
    aliases = get_rule("table.input_column_aliases")
    normalized: List[str] = []
    sources: List[str] = []
    used: Dict[str, int] = {}
    for position, header in enumerate(input_headers):
        key = normalize_key(header)
        mapped_key: Optional[str] = None
        for candidate_key, names in aliases.items():
            if key in {normalize_key(name) for name in names}:
                mapped_key = candidate_key
                break
        if mapped_key is not None:
            source = get_rule("table.provenance_sources.alias")
        elif key in {normalize_key(value["header"]) for value in get_rule("table.canonical_columns")}:
            mapped_key = next(value["key"] for value in get_rule("table.canonical_columns") if normalize_key(value["header"]) == key)
            source = get_rule("table.provenance_sources.canonical")
        elif get_rule("table.unknown_columns_positional_fallback") and position < len(get_rule("table.canonical_columns")):
            mapped_key = get_rule("table.canonical_columns")[position]["key"]
            source = get_rule("table.provenance_sources.positional")
        else:
            raise InvalidSpecResponse("column mapping failed; " + rule_basis("normalization.fallback_order"))
        if mapped_key in used:
            raise InvalidSpecResponse("duplicate normalized column: {0}".format(mapped_key))
        used[mapped_key] = position
        normalized.append(_column_by_key(mapped_key)["header"])
        sources.append(source)
    return tuple(normalized), tuple(sources), rule_basis("normalization.order")


def _extract_path(payload: Dict[str, Any], paths: Sequence[Sequence[str]]) -> Tuple[bool, Any, Optional[Sequence[str]]]:
    for path in paths:
        current: Any = payload
        found = True
        for key in path:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                found = False
                break
        if found:
            return True, current, path
    return False, None, None


def _quote_date(quote: Dict[str, Any]) -> dt.datetime:
    found, value, _ = _extract_path(quote, _column_by_key("date")["paths"])
    if not found:
        raise InvalidSpecResponse("required date field missing; " + rule_basis("table.missing_required_policy"))
    return parse_iso_utc(value)


def extract_quotes(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    quotes = get_path(response, get_rule("windows.upstream_quotes_path").split("."))
    if not isinstance(quotes, list):
        raise InvalidSpecResponse(rule_basis("windows.upstream_quotes_path"))
    return quotes


def parse_quote_table(response: Dict[str, Any], source_kind: str) -> ParsedTable:
    if source_kind not in {get_rule("source_policy.snapshot_marker"), get_rule("source_policy.live_marker")}:
        raise SpecIntegrityError("unknown response source_kind: {0}".format(source_kind))
    rows: List[Tuple[Any, ...]] = []
    field_sources: Dict[str, List[str]] = {column["key"]: [] for column in get_rule("table.canonical_columns")}
    markers: List[str] = []
    for quote in extract_quotes(response):
        row: List[Any] = []
        for column in get_rule("table.canonical_columns"):
            found, value, path = _extract_path(quote, column["paths"])
            if not found:
                if column["required"]:
                    raise InvalidSpecResponse("required {0} field missing; ".format(column["key"]) + rule_basis("table.missing_required_policy"))
                value = get_rule("table.default_values")[column["kind"]]
                if source_kind == get_rule("source_policy.snapshot_marker"):
                    source = get_rule("table.provenance_sources.default")
                    markers.append(get_rule("source_policy.snapshot_defaults_marker"))
                else:
                    source = get_rule("table.provenance_sources.missing")
                    markers.append(get_rule("source_policy.live_missing_marker"))
            else:
                source = get_rule("table.provenance_sources.canonical")
                if column["kind"] == "date":
                    value = format_spec_date(parse_iso_utc(value))
                elif column["kind"] == "timestamp":
                    parse_iso_utc(value)
            row.append(value)
            field_sources[column["key"]].append(source)
        rows.append(tuple(row))
    rows.sort(key=lambda row: dt.datetime.strptime(row[0], get_rule("time.date_output_format")).date(), reverse=True)
    markers = sorted(set(markers))
    return ParsedTable(
        canonical_headers(), tuple(rows),
        {key: tuple(values) for key, values in field_sources.items()},
        tuple(markers), rule_basis("windows.row_order"),
    )


def parse_snapshot_generated_at(snapshot: Dict[str, Any]) -> dt.datetime:
    value = get_path(snapshot, get_rule("source_policy.snapshot_generated_at_path"))
    return parse_iso_utc(value)


def _response_in_window(response: Dict[str, Any], window: RequestWindow) -> Dict[str, Any]:
    filtered = json.loads(json.dumps(response))
    data = get_path(filtered, ("data",))
    if not isinstance(data, dict):
        raise InvalidSpecResponse(rule_basis("windows.upstream_quotes_path"))
    data["quotes"] = [quote for quote in extract_quotes(filtered) if window.start.date() <= _quote_date(quote).date() <= window.end.date()]
    return filtered


def snapshot_coverage(snapshot: Dict[str, Any], window: RequestWindow) -> Tuple[bool, Tuple[str, ...]]:
    dates = {_quote_date(quote).date() for quote in extract_quotes(snapshot)}
    expected = {(window.start + dt.timedelta(days=offset)).date() for offset in range(window.span_days + 1)}
    missing = tuple(sorted((expected - dates)))
    return not missing, tuple(format_spec_date(_utc_midnight(day)) for day in missing)


def default_records(table: ParsedTable) -> Tuple[Dict[str, Any], ...]:
    records: List[Dict[str, Any]] = []
    for key, sources in table.field_sources.items():
        for index, source in enumerate(sources):
            if source == get_rule("table.provenance_sources.default"):
                records.append({"row": index, "date": table.rows[index][0], "field": _column_by_key(key)["header"], "value": None, "source": source})
    return tuple(records)


def reconcile_sources(
    snapshot: Optional[Dict[str, Any]],
    live_response: Optional[Dict[str, Any]],
    window: RequestWindow,
    now: dt.datetime,
    live_failure: bool = False,
) -> SourceDecision:
    basis = [
        rule_basis("source_policy.complete_coverage_required"),
        rule_basis("source_policy.snapshot_preferred_max_request_span_days"),
        rule_basis("source_policy.snapshot_fresh_max_age_days"),
        rule_basis("source_policy.conflict_winner"),
    ]
    live_table = parse_quote_table(live_response, get_rule("source_policy.live_marker")) if live_response is not None else None

    if snapshot is not None:
        bounded_snapshot = _response_in_window(snapshot, window)
        generated_at = parse_snapshot_generated_at(snapshot)
        age_days = (now - generated_at).days
        coverage_ok, missing = snapshot_coverage(bounded_snapshot, window)
        span_ok = window.span_days <= get_rule("source_policy.snapshot_preferred_max_request_span_days")
        age_ok = 0 <= age_days <= get_rule("source_policy.snapshot_fresh_max_age_days")
        eligible = coverage_ok and span_ok and age_ok
        if eligible or (live_failure and coverage_ok and span_ok and age_days >= 0):
            snapshot_table = parse_quote_table(bounded_snapshot, get_rule("source_policy.snapshot_marker"))
            markers = [get_rule("source_policy.snapshot_marker")]
            if live_failure:
                markers.append(get_rule("source_policy.fallback_marker"))
            if not age_ok:
                markers.append(get_rule("source_policy.stale_fallback_marker"))
            if live_table is not None:
                conflicts: List[str] = []
                for index, live_row in enumerate(live_table.rows):
                    if index >= len(snapshot_table.rows):
                        conflicts.append("live row {0} absent from snapshot".format(index))
                        continue
                    snapshot_row = snapshot_table.rows[index]
                    for position, (snapshot_value, live_value) in enumerate(zip(snapshot_row, live_row)):
                        if snapshot_value != live_value:
                            conflicts.append("{0}[{1}]: snapshot={2!r} live={3!r}".format(snapshot_table.headers[position], index, snapshot_value, live_value))
                if len(live_table.rows) != len(snapshot_table.rows):
                    conflicts.append("row count: snapshot={0} live={1}".format(len(snapshot_table.rows), len(live_table.rows)))
                if snapshot_table.markers:
                    markers.extend(snapshot_table.markers)
                if conflicts:
                    markers.append(get_rule("source_policy.degraded_marker_on_conflict"))
                return SourceDecision(bounded_snapshot, get_rule("source_policy.snapshot_marker"), tuple(markers), tuple(conflicts), default_records(snapshot_table), tuple(basis))
            return SourceDecision(
                bounded_snapshot, get_rule("source_policy.snapshot_marker"),
                tuple(markers) + snapshot_table.markers,
                (), default_records(snapshot_table), tuple(basis),
            )
        reason = "coverage" if not coverage_ok else "span" if not span_ok else "age"
        basis.append("spec:source_policy.ineligible_reason={!r}; missing={!r}".format(reason, missing))

    if live_response is not None:
        bounded_live = _response_in_window(live_response, window)
        assert live_table is not None
        return SourceDecision(
            bounded_live, get_rule("source_policy.live_marker"),
            (get_rule("source_policy.live_marker"),) + live_table.markers,
            (), (), tuple(basis),
        )

    raise InvalidSpecResponse("no live response or builtin snapshot is available")


def validate_spec() -> None:
    headers = canonical_headers()
    if len(headers) != len(set(headers)):
        raise SpecIntegrityError("duplicate canonical table header")
    required_aliases = set()
    symbol_counts: Dict[str, int] = {}
    for coin in get_rule("coins.canonical"):
        symbol_key = normalize_key(coin["symbol"])
        symbol_counts[symbol_key] = symbol_counts.get(symbol_key, 0) + 1
    for coin in get_rule("coins.canonical"):
        symbol_key = normalize_key(coin["symbol"])
        if symbol_counts[symbol_key] == 1 and symbol_key not in {normalize_key(alias) for alias in coin["aliases"]}:
            raise SpecIntegrityError("canonical symbol must have a matching alias: {0}".format(coin["symbol"]))
        for alias in coin["aliases"]:
            key = normalize_key(alias)
            if key in required_aliases:
                raise SpecIntegrityError("duplicate coin alias: {0}".format(alias))
            required_aliases.add(key)
    aliases = get_rule("table.input_column_aliases")
    canonical_keys = {column["key"] for column in get_rule("table.canonical_columns")}
    if set(aliases) != canonical_keys:
        raise SpecIntegrityError("column aliases must cover every canonical column exactly once")
    signature = get_rule("compatibility.crypto_data_legacy_signature").split(",")
    retained = get_rule("compatibility.legacy_fields_must_remain")
    if signature != retained or get_rule("compatibility.legacy_positional_arity") != len(retained):
        raise SpecIntegrityError("CryptoData legacy signature drift")
    if get_rule("compatibility.maximum_positional_arity") != get_rule("compatibility.legacy_positional_arity"):
        raise SpecIntegrityError("new fields must remain keyword-only")


validate_spec()








