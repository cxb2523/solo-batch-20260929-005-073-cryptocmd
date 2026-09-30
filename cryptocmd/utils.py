#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function

import datetime

from requests import get

from . import spec

# Re-export the specification exceptions so the historical public API
# (`from cryptocmd.utils import InvalidParameters, InvalidCoinCode`) keeps
# working while their actual definition lives in the spec.
InvalidParameters = spec.InvalidParameters
InvalidCoinCode = spec.InvalidCoinCode

_CMC_DATA_API = "https://api.coinmarketcap.com/data-api/v3"
_HEADERS = {"User-Agent": "Mozilla/5.0"}


def get_url_data(url):
    """Download a URL and return the response object."""
    try:
        response = get(url, headers=_HEADERS)
        return response
    except Exception as exc:  # pragma: no cover - network failure
        print("Error message (get_url_data) :", exc)
        raise


def _listing_matches(coin, identity):
    """A listing matches when its symbol/name agree with the SPEC identity.

    Basis SPEC-COIN-01: compare against the canonical code/name, which already
    incorporates alias normalization, so an alias like XBT matches a BTC listing.
    """
    symbol_ok = identity["canonical_code"] is not None and str(
        coin.get("symbol", "")
    ).upper() == identity["canonical_code"].upper()
    if identity["requested_name"] is None:
        return symbol_ok
    wanted = spec._key(identity["requested_name"])
    name_ok = spec._key(coin.get("name", "")) == wanted
    return symbol_ok and name_ok


def get_coin_id(coin_code, coin_name, transport=get_url_data):
    """Resolve a coin code/name to its numeric CMC id (SPEC-COIN-01)."""
    identity = spec.normalize_coin(coin_code, coin_name)
    api_url = (
        "{}/cryptocurrency/listing?start=1&limit=5000"
        "&sortBy=market_cap&sortType=desc&convert=USD"
        "&cryptoType=all&tagType=all&audited=false"
    ).format(_CMC_DATA_API)

    try:
        json_data = transport(api_url).json()
        error_code = json_data["status"]["error_code"]
        if str(error_code) == "0":
            coins = json_data["data"]["cryptoCurrencyList"]
            matches = [c for c in coins if _listing_matches(c, identity)]
            if matches:
                return matches[0]["id"]
            raise InvalidCoinCode(
                "'{}' coin code is unavailable on coinmarketcap.com".format(
                    coin_code
                )
            )
        if str(error_code) == "400":
            raise InvalidCoinCode(
                "'{}' coin code is unavailable on coinmarketcap.com".format(
                    coin_code
                )
            )
        raise Exception(json_data["status"]["error_message"])
    except Exception as exc:
        print(
            "Error fetching coin id data for coin code {}".format(coin_code)
        )
        print("Error message:", exc)
        raise


def _now():
    return datetime.datetime.now(tz=datetime.timezone.utc)


def _build_result_envelope(identity, quotes, coin_id):
    return {
        "status": {"error_code": 0, "error_message": None},
        "data": {
            "id": coin_id,
            "symbol": identity["canonical_code"],
            "name": identity["canonical_name"],
            "quotes": quotes,
            "__cell_sources__": [],
        },
    }


def _read_snapshot(identity, window, now, bundle=None):
    """Read quotes from the built-in snapshot for the closed request range.

    Applies SPEC-SNAP-01 default-fill/drop via spec.parse_quote_row and returns
    raw-shaped quotes (with defaults materialised) plus a provenance report.
    """
    bundle = spec.load_snapshot_bundle() if bundle is None else bundle
    if bundle is None:
        return None
    entry = bundle.get("coins", {}).get(identity["canonical_code"])
    if entry is None:
        return None

    age_days = spec.snapshot_age_days(bundle, now=now)
    covered = spec.snapshot_covers(bundle, identity["canonical_code"], window)

    kept_quotes = []
    kept_sources = []
    fills = 0
    dropped = 0
    wanted_days = set()
    cursor = window["start_dt"].date()
    while cursor <= window["end_dt"].date():
        wanted_days.add(cursor)
        cursor += datetime.timedelta(days=1)

    for quote in entry.get("quotes", []):
        raw = quote.get(spec.MANDATORY_KEY)
        if raw is None:
            dropped += 1
            continue
        day = datetime.datetime.strptime(
            raw, spec.API_TIME_FORMAT
        ).date()
        if day not in wanted_days:
            continue
        values, cell_sources, is_dropped = spec.parse_quote_row(
            quote, "snapshot"
        )
        if is_dropped:
            dropped += 1
            continue
        fills += sum(1 for tag in cell_sources if tag == "snapshot.default")
        kept_quotes.append(_materialize_snapshot_quote(quote, values))
        kept_sources.append(cell_sources)

    return {
        "quotes": kept_quotes,
        "cell_sources": kept_sources,
        "age_days": age_days,
        "covered": covered,
        "fills": fills,
        "dropped": dropped,
        "generated_at": bundle.get("generated_at"),
    }


def _materialize_snapshot_quote(original, values):
    """Rebuild a quote dict from parsed ordered values (Date is derived)."""
    header_to_value = dict(zip(spec.HEADERS, values))
    return {
        "timeOpen": original.get("timeOpen"),
        "timeHigh": header_to_value.get("Time High"),
        "timeLow": header_to_value.get("Time Low"),
        "timeClose": header_to_value.get("Time Close"),
        "quote": {
            "open": header_to_value.get("Open"),
            "high": header_to_value.get("High"),
            "low": header_to_value.get("Low"),
            "close": header_to_value.get("Close"),
            "volume": header_to_value.get("Volume"),
            "marketCap": header_to_value.get("Market Cap"),
        },
    }


def download_coin_data(
    coin_code,
    start_date,
    end_date,
    fiat,
    coin_name,
    id_number=None,
    transport=None,
    now=None,
    snapshot_bundle=None,
    force_source=None,
):
    """Download price history, evaluating every rule against ``spec``.

    New keyword-only seams (default behaviour is unchanged):
      transport       - callable(url)->response with .json() (replay injection)
      now             - injectable current UTC datetime (deterministic replay)
      snapshot_bundle - override the built-in snapshot bundle
      force_source    - "live"|"snapshot" to bypass SPEC-SRC-01 (audit only)

    Returns the JSON-shaped envelope with an extra ``__provenance__`` key
    describing the source decision and any snapshot fills/drops.
    """
    transport = transport or get_url_data
    now = now or _now()

    if start_date is None:
        start_date = spec.DEFAULT_START_DATE
    if end_date is None:
        end_date = spec.default_end_date(today=now.date())

    # SPEC-WIN-01: build/validate the closed range -> half-open wire window.
    # span == 0 is allowed; span < 0 raises InvalidParameters here.
    window = spec.build_window(start_date, end_date)

    # SPEC-FIAT-01: resolve fiat by the fixed priority, never hard-coded.
    convert_id, canonical_fiat, fiat_source = spec.resolve_fiat(fiat)

    identity = spec.normalize_coin(coin_code, coin_name)

    # SPEC-SNAP-01 / SPEC-SRC-01: read snapshot signals before deciding.
    snap = _read_snapshot(
        identity, window, now=now, bundle=snapshot_bundle
    )
    snapshot_available = bool(snap and snap["covered"])
    age_days = snap["age_days"] if snap else None
    decision = spec.decide_source(
        window["span_days"], age_days, snapshot_available
    )
    chosen = force_source or decision["source"]
    if force_source:
        decision = dict(decision)
        decision["forced"] = force_source

    provenance = {
        "source": chosen,
        "degraded": bool(decision.get("degraded")),
        "reason": decision.get("reason"),
        "basis": decision.get("basis"),
        "span_days": window["span_days"],
        "snapshot_age_days": age_days,
        "snapshot_covered": snapshot_available,
        "span_votes": decision.get("span_votes"),
        "age_votes": decision.get("age_votes"),
        "coin": identity,
        "fiat": {"code": canonical_fiat, "id": convert_id, "source": fiat_source},
        "window": {
            "time_start_exclusive": int(window["wire_start"].timestamp()),
            "time_end_inclusive": int(window["wire_end"].timestamp()),
            "units": "seconds",
        },
        "snapshot_fills": 0,
        "snapshot_dropped": 0,
        "snapshot_generated_at": None,
        "chunk_boundaries": [],
    }

    if chosen == "snapshot":
        if not snapshot_available:
            # A forced/decided snapshot without coverage cannot be served.
            raise InvalidParameters(
                "Snapshot does not cover the requested range (SPEC-SNAP-01)."
            )
        coin_id = id_number
        result_json = _build_result_envelope(
            identity, snap["quotes"], coin_id
        )
        result_json["data"]["__cell_sources__"] = snap["cell_sources"]
        provenance["snapshot_fills"] = snap["fills"]
        provenance["snapshot_dropped"] = snap["dropped"]
        provenance["snapshot_generated_at"] = snap["generated_at"]
        result_json["__provenance__"] = provenance
        return result_json

    coin_id = id_number if id_number else get_coin_id(
        coin_code, coin_name, transport=transport
    )
    return _download_live(
        window, coin_id, convert_id, identity, transport, provenance
    )


def _download_live(window, coin_id, convert_id, identity, transport, provenance):
    """Fetch yearly half-open chunks and merge (SPEC-WIN-01)."""
    try:
        all_quotes = []
        result_json = None
        for chunk_start, chunk_end in spec.iter_chunks(window):
            # Wire units are fixed UTC epoch SECONDS (SPEC-WIN-01).
            time_start = int(chunk_start.timestamp())
            time_end = int(chunk_end.timestamp())
            spec.assert_same_units(time_start, time_end)
            provenance["chunk_boundaries"].append([time_start, time_end])
            api_url = (
                "{}/cryptocurrency/historical"
                "?id={}&convertId={}&timeStart={}&timeEnd={}"
            ).format(
                _CMC_DATA_API, coin_id, convert_id, time_start, time_end
            )
            json_data = transport(api_url).json()
            status = json_data.get("status", {})
            error_code = status.get("error_code")
            if error_code is not None and str(error_code) != "0":
                raise Exception(status.get("error_message", "Unknown error"))
            if result_json is None:
                result_json = json_data
            all_quotes.extend(json_data["data"]["quotes"])

        result_json["data"]["quotes"] = all_quotes
        live_sources = []
        for quote in all_quotes:
            _vals, tags, _dropped = spec.parse_quote_row(quote, "live")
            live_sources.append(tags or [])
        result_json["data"]["__cell_sources__"] = live_sources

        if identity["requested_code"] is not None and result_json["data"].get(
            "symbol"
        ) and identity["requested_code"].upper() != result_json["data"][
            "symbol"
        ].upper():
            print(
                "INFO: id used; requested coin_code ({}) differs from "
                "returned symbol.".format(identity["requested_code"])
            )

        result_json["__provenance__"] = provenance
        return result_json
    except Exception as exc:
        print("Error message (download_data) :", exc)
        raise
