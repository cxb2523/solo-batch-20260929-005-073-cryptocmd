#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function

import json
import os
import sys

from requests import get

from .spec import (
    BUILTIN_SNAPSHOT_PATH,
    AmbiguousCoinIdentity,
    InvalidSpecResponse,
    InvalidSpecWindow,
    get_path,
    get_rule,
    format_spec_date,
    reconcile_sources,
    resolve_coin,
    resolve_fiat,
    rule_basis,
    supported_fiat_codes,
    to_unix_seconds,
    utc_today_midnight,
    validate_request_window,
    iter_upstream_windows,
)


class InvalidParameters(ValueError):
    """Passed parameters are invalid."""


class InvalidCoinCode(NotImplementedError):
    """This coin code is unavailable on 'coinmarketcap.com'"""


def get_url_data(url):
    """Download a URL using request headers declared by the executable spec."""
    try:
        return get(url, headers=get_rule("request.http_headers"))
    except Exception as exc:
        print("Error message (get_url_data) :", exc)
        raise


def _api_url(path):
    return get_rule("request.api_base_url") + get_rule(path)


def _status_value(response, key):
    return get_path(response, get_rule("request.status_" + key + "_path"))


def _listing_coins(response):
    coins = get_path(response, tuple(get_rule("request.listing_response_path").split(".")))
    if not isinstance(coins, list):
        raise InvalidSpecResponse(rule_basis("request.listing_response_path"))
    return coins


def get_coin_id(coin_code, coin_name):
    """Resolve a coin id from the canonical spec, falling back to a live listing."""
    identity = resolve_coin(coin_code=coin_code, coin_name=coin_name)
    if identity.coin_id is not None:
        return identity.coin_id

    fiat = resolve_fiat(get_rule("fiat_ids.default_listing_convert_code"))
    query = get_rule("request.listing_query").format(convert=fiat.code)
    api_url = _api_url("request.listing_path") + "?" + query
    json_data = get_url_data(api_url).json()
    error_code = _status_value(json_data, "error_code")
    if error_code not in get_rule("request.success_error_codes"):
        if error_code in get_rule("request.not_found_error_codes"):
            raise InvalidCoinCode(
                "'{}' coin code is unavailable on coinmarketcap.com".format(coin_code)
            )
        raise Exception(_status_value(json_data, "error_message") or "Unknown error")

    symbol_field = get_rule("request.coin_symbol_field")
    name_field = get_rule("request.coin_name_field")
    id_field = get_rule("request.coin_id_field")
    code_key = str(coin_code).strip().lower()
    name_key = str(coin_name).strip().lower() if coin_name is not None else None
    matches = [
        coin for coin in _listing_coins(json_data)
        if str(coin.get(symbol_field, "")).lower() == code_key
        and (name_key is None or str(coin.get(name_field, "")).lower() == name_key)
    ]
    if matches:
        return matches[0][id_field]
    raise InvalidCoinCode(
        "'{}' coin code is unavailable on coinmarketcap.com".format(coin_code)
    )


def _resolve_coin(coin_code, coin_name, id_number):
    if id_number:
        return resolve_coin(coin_code=coin_code, coin_name=coin_name, id_number=id_number)
    identity = resolve_coin(coin_code=coin_code, coin_name=coin_name)
    if identity.coin_id is None:
        coin_id = get_coin_id(coin_code, coin_name)
        return resolve_coin(coin_code=coin_code, coin_name=coin_name, id_number=coin_id)
    return identity


def _load_builtin_snapshot():
    if not os.path.exists(BUILTIN_SNAPSHOT_PATH):
        return None
    with open(BUILTIN_SNAPSHOT_PATH, encoding="utf-8") as snapshot_file:
        return json.load(snapshot_file)


def _request_historical_page(coin_id, convert_id, chunk_start, chunk_end):
    query = get_rule("request.historical_query").format(
        id=coin_id,
        convert_id=convert_id,
        time_start=to_unix_seconds(chunk_start),
        time_end=to_unix_seconds(chunk_end),
    )
    api_url = _api_url("request.historical_path") + "?" + query
    json_data = get_url_data(api_url).json()
    error_code = _status_value(json_data, "error_code")
    if error_code not in get_rule("request.success_error_codes"):
        raise Exception(_status_value(json_data, "error_message") or "Unknown error")
    return json_data


def _download_live(identity, fiat, window):
    result_json = None
    all_quotes = []
    for chunk_start, chunk_end in iter_upstream_windows(window):
        page = _request_historical_page(
            identity.coin_id, fiat.convert_id, chunk_start, chunk_end
        )
        if result_json is None:
            result_json = page
        quotes = get_path(page, tuple(get_rule("windows.upstream_quotes_path").split(".")))
        if not isinstance(quotes, list):
            raise InvalidSpecResponse(rule_basis("windows.upstream_quotes_path"))
        all_quotes.extend(quotes)
    if result_json is None:
        raise InvalidSpecResponse("historical request produced no pages")
    result_json["data"]["quotes"] = all_quotes
    return result_json


def _report_id_mismatch(identity, result_json):
    if identity.source != "input.id_number":
        return
    data = result_json.get("data", {})
    show_coin_info = False
    if identity.canonical_symbol and identity.canonical_symbol.upper() != data.get("symbol", "").upper():
        print("INFO: Using 'id_number'! The 'coin_code' ({}) provided is different from the symbol returned.".format(identity.canonical_symbol))
        show_coin_info = True
    if identity.canonical_name and identity.canonical_name.lower() != data.get("name", "").lower():
        print("INFO: Using 'id_number'! The 'coin_name' ({}) provided is different from the name returned.".format(identity.canonical_name))
        show_coin_info = True
    if show_coin_info:
        print('The returned data belongs to coin "{name}", with symbol "{symbol}"'.format(**data))


def download_coin_data(coin_code, start_date, end_date, fiat, coin_name, id_number=None):
    """Download price history using every mapping and boundary from spec.py."""
    original_start = start_date
    original_end = end_date
    if start_date is None:
        start_date = get_rule("time.default_all_time_start")
    if end_date is None:
        end_date = format_spec_date(utc_today_midnight())

    try:
        window = validate_request_window(start_date, end_date)
        fiat_identity = resolve_fiat(fiat)
        identity = _resolve_coin(coin_code, coin_name, id_number)
    except (InvalidSpecWindow, AmbiguousCoinIdentity, ValueError):
        raise

    snapshot = _load_builtin_snapshot()
    snapshot_data = snapshot.get("data", {}) if snapshot else {}
    if snapshot is not None and snapshot_data.get("id") != identity.coin_id:
        snapshot = None
    try:
        live_json = _download_live(identity, fiat_identity, window)
        decision = reconcile_sources(snapshot, live_json, window, utc_today_midnight())
    except Exception as live_error:
        if snapshot is None:
            print("Error fetching price data for {}".format(id_number or coin_code))
            print("Error message (download_data) :", live_error)
            raise
        try:
            decision = reconcile_sources(
                snapshot, None, window, utc_today_midnight(), live_failure=True
            )
        except Exception:
            print("Error fetching price data for {}".format(id_number or coin_code))
            print("Error message (download_data) :", live_error)
            raise

    result_json = dict(decision.selected_response)
    result_json.setdefault("status", {"error_code": 0, "error_message": None})
    result_json["_cryptocmd"] = {
        "source": decision.selected_source,
        "markers": list(decision.markers),
        "conflicts": list(decision.conflicts),
        "default_records": list(decision.default_records),
        "basis": list(decision.basis),
        "aliases": list(identity.aliases),
        "coin_source": identity.source,
        "coin_basis": identity.basis,
        "fiat_source": fiat_identity.source,
        "fiat_basis": fiat_identity.basis,
        "window_basis": window.basis,
        "requested_start": original_start,
        "requested_end": original_end,
    }
    _report_id_mismatch(identity, result_json)
    return result_json


def _replace(s, bad_chars):
    if sys.version_info > (3, 0):
        return s.translate(str.maketrans("", "", bad_chars))
    import string

    return s.translate(string.maketrans("", ""), bad_chars)


__all__ = [
    "download_coin_data",
    "get_coin_id",
    "get_url_data",
    "supported_fiat_codes",
    "InvalidParameters",
    "InvalidCoinCode",
]
