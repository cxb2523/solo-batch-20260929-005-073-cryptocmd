#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Cryptocurrency price history from coinmarketcap.com (spec-driven)."""

from __future__ import print_function

__all__ = ["CmcScraper", "CryptoData"]

import csv
import os
import warnings
from datetime import datetime

import tablib

from . import spec
from .utils import download_coin_data, InvalidParameters


class CryptoData(object):
    """A single parsed OHLCV row.

    SPEC-CDATA-01: the legacy POSITIONAL constructor is preserved exactly
    (date, open, high, low, close, volume, market_cap, time_open, time_high,
    time_low, time_close). New alias/provenance fields are keyword-only, so any
    old positional call site keeps working. ``tools/spec_replay.py --strict``
    instantiates both ways and fails the whole run if this breaks.
    """

    LEGACY_FIELDS = (
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "market_cap",
        "time_open",
        "time_high",
        "time_low",
        "time_close",
    )

    def __init__(self, date, open, high, low, close, volume, market_cap,
                 time_open, time_high, time_low, time_close,
                 alias=None, canonical_code=None, canonical_name=None,
                 source=None, cell_sources=None):
        self.date = date
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.market_cap = market_cap
        self.time_open = time_open
        self.time_high = time_high
        self.time_low = time_low
        self.time_close = time_close
        # New alias/provenance fields (SPEC-CDATA-01 / SPEC-PROV-01).
        self.alias = alias
        self.canonical_code = canonical_code
        self.canonical_name = canonical_name
        self.source = source
        self.cell_sources = list(cell_sources) if cell_sources else []

    @property
    def is_filled_default(self):
        """True if any cell was filled from a SPEC snapshot default."""
        return any(tag == "snapshot.default" for tag in self.cell_sources)

    def as_row(self):
        """Return the canonical ordered list used by the table (SPEC-COL-01)."""
        return [
            self.date,
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume,
            self.market_cap,
            self.time_open,
            self.time_high,
            self.time_low,
            self.time_close,
        ]

    @classmethod
    def from_row(cls, values, identity=None, source=None, cell_sources=None):
        """Build CryptoData from an ordered canonical row (SPEC-CDATA-01)."""
        identity = identity or {}
        return cls(
            *(values + [None] * (len(cls.LEGACY_FIELDS) - len(values))),
            alias=identity.get("alias"),
            canonical_code=identity.get("canonical_code"),
            canonical_name=identity.get("canonical_name"),
            source=source,
            cell_sources=cell_sources,
        )

    def __eq__(self, other):
        if not isinstance(other, CryptoData):
            return NotImplemented
        return self.as_row() == other.as_row()

    def __repr__(self):
        return "<CryptoData {0} {1} close={2} source={3}>".format(
            self.canonical_code, self.date, self.close, self.source
        )


class CmcScraper(object):
    """Scrape cryptocurrency historical market price data (spec-driven)."""

    def __init__(
        self,
        coin_code=None,
        start_date=None,
        end_date=None,
        all_time=False,
        order_ascending=False,
        fiat="USD",
        coin_name=None,
        id_number=None,
    ):
        self.coin_code = coin_code
        self.start_date = start_date
        self.end_date = end_date
        self.all_time = bool(all_time)
        self.order_ascending = order_ascending
        self.fiat = fiat
        self.coin_name = coin_name
        self.id_number = id_number

        # SPEC-COL-01: headers come from the specification, never hard-coded.
        self.headers = list(spec.HEADERS)
        self.rows = []
        # Parsed per-row records (CryptoData) and per-cell provenance tags.
        self.records = []
        self.cell_sources = []
        self.provenance = None

        if not (self.start_date and self.end_date):
            self.all_time = True

        if not (self.all_time or (self.start_date and self.end_date)):
            raise InvalidParameters(
                "'start_date' or 'end_date' cannot be empty if 'all_time' "
                "flag is False"
            )

    def __repr__(self):
        return (
            "<CmcScraper coin_code:{}, start_date:{}, end_date:{}, "
            "all_time:{}>".format(
                self.coin_code,
                self.start_date,
                self.end_date,
                self.all_time,
            )
        )

    def _download_data(self, **kwargs):
        """Download and parse data; the value-taking path is spec-driven."""
        forced = kwargs.get("forced")
        if self.rows and not forced:
            return

        if self.all_time:
            self.start_date, self.end_date = None, None

        coin_data = download_coin_data(
            self.coin_code,
            self.start_date,
            self.end_date,
            self.fiat,
            self.coin_name,
            self.id_number,
            transport=kwargs.get("transport"),
            now=kwargs.get("now"),
            snapshot_bundle=kwargs.get("snapshot_bundle"),
            force_source=kwargs.get("force_source"),
        )
        self.provenance = coin_data.get("__provenance__", {})
        source = self.provenance.get("source", "live")
        identity = self.provenance.get("coin") or spec.normalize_coin(
            self.coin_code, self.coin_name
        )

        parse_source = "snapshot" if source == "snapshot" else "live"
        self.records = []
        self.cell_sources = []
        self.rows = []

        raw_quotes = coin_data["data"]["quotes"]
        provided_sources = coin_data["data"].get("__cell_sources__") or []

        # SPEC-NORM-01: the single pipeline - coin alias first, then columns.
        for index, raw_quote in enumerate(raw_quotes):
            parsed = spec.normalize_record(
                raw_quote, self.coin_code, self.coin_name, parse_source
            )
            if parsed["dropped"]:
                continue
            values = parsed["values"]
            # For snapshots, use the authoritative fill/drop tags produced at
            # read time so spec defaults stay distinguishable (SPEC-PROV-01).
            tags = (
                provided_sources[index]
                if index < len(provided_sources)
                else parsed["cell_sources"]
            )
            record = CryptoData.from_row(
                values,
                identity=identity,
                source=source,
                cell_sources=tags,
            )
            self.records.append(record)
            # API returns newest-first; emulate the original insert(0) reversal.
            self.rows.insert(0, values)
            self.cell_sources.insert(0, tags)

        if self.rows:
            self.end_date, self.start_date = self.rows[0][0], self.rows[-1][0]

        if self.order_ascending:
            order = sorted(
                range(len(self.rows)),
                key=lambda idx: datetime.strptime(
                    self.rows[idx][0], spec.OUTPUT_DATE_FORMAT
                ),
            )
            self.rows = [self.rows[i] for i in order]
            self.cell_sources = [self.cell_sources[i] for i in order]
            self.records = [self.records[i] for i in order]

    def get_data(self, format="", verbose=False, **kwargs):
        """Return downloaded data (headers, rows) or an exported string."""
        self._download_data(**kwargs)
        if verbose:
            print(*self.headers, sep=", ")
            for row in self.rows:
                print(*row, sep=", ")
        elif format:
            data = tablib.Dataset()
            data.headers = self.headers
            for row in self.rows:
                data.append(row)
            return data.export(format)
        else:
            return self.headers, self.rows

    def get_dataframe(self, date_as_index=False, **kwargs):
        """Return the data as a pandas DataFrame (SPEC-COL-01 column names)."""
        try:
            import pandas as pd
        except ImportError:
            pd = None

        if pd is None:
            raise NotImplementedError(
                "DataFrame Format requires 'pandas' to be installed."
                "Try : pip install pandas"
            )

        self._download_data(**kwargs)
        dataframe = pd.DataFrame(data=self.rows, columns=self.headers)
        date_header = spec.HEADERS[0]
        dataframe[date_header] = pd.to_datetime(
            dataframe[date_header],
            format=spec.OUTPUT_DATE_FORMAT,
            dayfirst=True,
        )
        if date_as_index:
            dataframe.set_index(date_header, inplace=True)
        return dataframe

    def export_csv(self, csv_name=None, csv_path=None, **kwargs):
        """Export data to CSV (deprecated; use export('csv'))."""
        warnings.warn(
            "export_csv will be deprecated; Use 'export' method instead, "
            "e.g. export('csv')",
            PendingDeprecationWarning,
            stacklevel=2,
        )
        self._download_data(**kwargs)
        if csv_path is None:
            csv_path = os.getcwd()
        if csv_name is None:
            csv_name = "{0}_{1}_{2}_{3}.csv".format(
                self.coin_code, self.fiat, self.start_date, self.end_date
            )
        if not csv_name.endswith(".csv"):
            csv_name += ".csv"
        _csv = "{0}/{1}".format(csv_path, csv_name)

        try:
            with open(_csv, "w", newline="", encoding="utf-8") as csvfile:
                writer = csv.writer(
                    csvfile, delimiter=",", quoting=csv.QUOTE_NONNUMERIC
                )
                writer.writerow(self.headers)
                for data in self.rows:
                    writer.writerow(data)
        except IOError as err:
            errno, strerror = err.args
            print("I/O error({0}): {1}".format(errno, strerror))

    def export(self, format, name=None, path=None, **kwargs):
        """Export data to the requested tablib file format."""
        data = self.get_data(format, **kwargs)
        if path is None:
            path = os.getcwd()
        if name is None:
            name = "{0}_{1}-{2}_{3}".format(
                self.coin_code, self.fiat, self.start_date, self.end_date
            )
        if not name.endswith(".{}".format(format)):
            name += ".{}".format(format)
        _file = "{0}/{1}".format(path, name)

        try:
            with open(_file, "wb") as handle:
                if isinstance(data, str):
                    handle.write(data.encode("utf-8"))
                else:
                    handle.write(data)
        except IOError as err:
            errno, strerror = err.args
            print("I/O error({0}): {1}".format(errno, strerror))
        except Exception as err:
            print("format: {0}, Error: {1}".format(format, err))
