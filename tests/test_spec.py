"""
Unit tests for the executable specification (cryptocmd.spec) and the
spec-driven scraper. All tests are offline and deterministic.
"""

import datetime
from unittest.mock import MagicMock

import pytest

from cryptocmd import spec
from cryptocmd.core import CryptoData
from cryptocmd.utils import download_coin_data


UTC = datetime.timezone.utc


def _response(payload):
    mock = MagicMock()
    mock.json.return_value = payload
    return mock


def _quote(day, quote=None):
    quote = quote or {
        "open": 1.0,
        "high": 2.0,
        "low": 0.5,
        "close": 1.5,
        "volume": 10.0,
        "marketCap": 100.0,
    }
    return {
        "timeOpen": "{0}T00:00:00.000Z".format(day),
        "timeHigh": "{0}T12:00:00.000Z".format(day),
        "timeLow": "{0}T06:00:00.000Z".format(day),
        "timeClose": "{0}T23:59:59.999Z".format(day),
        "quote": dict(quote),
    }


# --- SPEC-WIN-01 -----------------------------------------------------------


class TestWindowSemantics:
    def test_span_zero_is_valid(self):
        window = spec.build_window("20-01-2024", "20-01-2024")
        assert window["span_days"] == 0

    def test_span_negative_raises(self):
        with pytest.raises(spec.InvalidParameters):
            spec.build_window("25-01-2024", "20-01-2024")

    def test_half_open_endpoints(self):
        window = spec.build_window("20-01-2024", "25-01-2024")
        assert int(window["wire_start"].timestamp()) == (
            int(window["start_dt"].timestamp()) - 86400
        )
        assert int(window["wire_end"].timestamp()) == int(
            window["end_dt"].timestamp()
        )

    def test_chunks_are_adjacent_no_overlap_no_gap(self):
        window = spec.build_window("28-04-2013", "25-01-2024")
        chunks = list(spec.iter_chunks(window))
        assert len(chunks) > 1
        for prev, nxt in zip(chunks, chunks[1:]):
            assert prev[1] == nxt[0]

    def test_timestamp_unit_ms_scales_to_seconds(self):
        value, tag = spec.normalize_epoch(1705756800000)
        assert value == 1705756800
        assert tag == "unit.ms"

    def test_mixed_units_rejected(self):
        with pytest.raises(spec.TimestampUnitError):
            spec.assert_same_units(1705756800, 1705756800000)

    def test_microsecond_scale_rejected(self):
        with pytest.raises(spec.TimestampUnitError):
            spec.normalize_epoch(10 ** 15)


# --- SPEC-COIN-01 / SPEC-NORM-01 ------------------------------------------


class TestCoinAndColumnNormalization:
    def test_code_alias_resolves(self):
        identity = spec.normalize_coin("xbt")
        assert identity["canonical_code"] == "BTC"
        assert identity["source"] == "alias"
        assert identity["alias"] == "XBT"

    def test_unknown_code_is_literal_fallback(self):
        identity = spec.normalize_coin("zzz")
        assert identity["source"] == "fallback.literal"
        assert identity["canonical_code"] == "ZZZ"

    def test_per_code_name_alias(self):
        identity = spec.normalize_coin("sol", "solcoin")
        assert identity["canonical_code"] == "SOL"
        assert identity["canonical_name"] == "Solana"

    def test_column_alias_and_identity_fallback(self):
        assert spec.normalize_column_name("market_cap") == (
            "Market Cap",
            "alias",
        )
        header, tag = spec.normalize_column_name("Bogus")
        assert tag == "fallback.identity"
        assert header == "Bogus"

    def test_pipeline_order_is_coin_then_column(self):
        parsed = spec.normalize_record(_quote("2024-01-20"), "xbt", None, "live")
        assert parsed["stage_order"] == ("coin", "column")
        assert parsed["identity"]["canonical_code"] == "BTC"


# --- SPEC-FIAT-01 ----------------------------------------------------------


class TestFiatResolution:
    def test_exact_then_alias_then_error(self):
        assert spec.resolve_fiat("usd")[:2] == (2781, "USD")
        assert spec.resolve_fiat("euro")[:2] == (2790, "EUR")
        with pytest.raises(spec.InvalidFiat):
            spec.resolve_fiat("zzz")


# --- SPEC-SRC-01 / SPEC-SNAP-01 -------------------------------------------


class TestSourceDecisionAndSnapshot:
    def test_both_agree_snapshot(self):
        decision = spec.decide_source(5, 1, True)
        assert decision["source"] == "snapshot"
        assert decision["degraded"] is False

    def test_both_agree_live(self):
        decision = spec.decide_source(1000, 1000, True)
        assert decision["source"] == "live"
        assert decision["degraded"] is False

    def test_conflict_snapshot_wins_degraded(self):
        decision = spec.decide_source(100, 5, True)
        assert decision["source"] == "snapshot"
        assert decision["degraded"] is True

    def test_no_snapshot_coverage_falls_back_to_live(self):
        decision = spec.decide_source(5, 5, False)
        assert decision["source"] == "live"

    def test_snapshot_default_fill_and_drop(self):
        quote = {
            "timeOpen": "2024-01-22T00:00:00.000Z",
            "timeHigh": "2024-01-22T12:00:00.000Z",
            "timeLow": "2024-01-22T06:00:00.000Z",
            "timeClose": "2024-01-22T23:59:59.999Z",
            "quote": {"open": 1, "high": 2, "low": 1, "close": 2},
        }
        values, tags, dropped = spec.parse_quote_row(quote, "snapshot")
        assert dropped is False
        assert values[spec.HEADERS.index("Volume")] == 0.0
        assert tags[spec.HEADERS.index("Volume")] == "snapshot.default"
        _v, _t, dropped_missing = spec.parse_quote_row(
            {"quote": {}}, "snapshot"
        )
        assert dropped_missing is True


# --- SPEC-CDATA-01 ---------------------------------------------------------


class TestCryptoDataCompatibility:
    LEGACY = (
        "20-01-2024", 1.0, 2.0, 0.5, 1.5, 10.0, 100.0,
        "t1", "t2", "t3", "t4",
    )

    def test_legacy_positional_constructor(self):
        record = CryptoData(*self.LEGACY)
        assert record.as_row() == list(self.LEGACY)

    def test_new_alias_fields_coexist(self):
        record = CryptoData(
            *self.LEGACY,
            alias="XBT",
            canonical_code="BTC",
            canonical_name="Bitcoin",
            source="snapshot",
            cell_sources=["snapshot.default"],
        )
        assert record.alias == "XBT"
        assert record.canonical_code == "BTC"
        assert record.is_filled_default is True


# --- end-to-end source selection ------------------------------------------


class TestDownloadSourceSelection:
    NOW = datetime.datetime(2024, 1, 26, tzinfo=UTC)

    def transport(self, quotes):
        def _get(url):
            if "listing" in url:
                return _response(
                    {
                        "status": {"error_code": 0},
                        "data": {
                            "cryptoCurrencyList": [
                                {"id": 1, "symbol": "BTC", "name": "Bitcoin"}
                            ]
                        },
                    }
                )
            return _response(
                {
                    "status": {"error_code": 0},
                    "data": {
                        "id": 1,
                        "symbol": "BTC",
                        "name": "Bitcoin",
                        "quotes": quotes,
                    },
                }
            )

        return _get

    def test_fresh_short_range_uses_snapshot(self):
        envelope = download_coin_data(
            "BTC",
            "20-01-2024",
            "25-01-2024",
            "USD",
            None,
            id_number=1,
            transport=self.transport([]),
            now=self.NOW,
        )
        prov = envelope["__provenance__"]
        assert prov["source"] == "snapshot"
        assert prov["snapshot_fills"] == 3
        assert prov["snapshot_dropped"] == 1

    def test_long_stale_range_uses_live(self):
        quotes = [_quote("2024-01-2{0}".format(i)) for i in range(0, 3)]
        envelope = download_coin_data(
            "BTC",
            "01-01-2020",
            "02-01-2020",
            "USD",
            None,
            id_number=1,
            transport=self.transport(quotes),
            now=self.NOW,
        )
        prov = envelope["__provenance__"]
        assert prov["source"] == "live"
        assert prov["snapshot_covered"] is False
