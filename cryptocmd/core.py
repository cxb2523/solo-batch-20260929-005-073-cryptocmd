#!/usr/bin/env python
# -*- coding: utf-8 -*-
# __author__ = 'Rohit Gupta'

"""Cryptocurrency price history from coinmarketcap.com."""

from __future__ import print_function

__all__ = ["CmcScraper", "CryptoData"]

import csv
import inspect
import os
import warnings

import tablib

from .spec import (
    SpecIntegrityError,
    canonical_headers,
    get_rule,
    parse_spec_date,
    parse_quote_table,
)
from .utils import InvalidParameters, download_coin_data


class CryptoData(object):
    """Container preserving the historical ten-field positional constructor."""

    def __init__(
        self,
        coin_code,
        start_date,
        end_date,
        all_time,
        order_ascending,
        fiat,
        coin_name,
        id_number,
        headers,
        rows,
        *,
        aliases=(),
    ):
        self.coin_code = coin_code
        self.start_date = start_date
        self.end_date = end_date
        self.all_time = all_time
        self.order_ascending = order_ascending
        self.fiat = fiat
        self.coin_name = coin_name
        self.id_number = id_number
        self.headers = headers
        self.rows = rows
        self.aliases = tuple(aliases)
        self.source_decision = {}

    def __repr__(self):
        return "<CryptoData coin_code:{}, start_date:{}, end_date:{}, aliases:{}>".format(
            self.coin_code, self.start_date, self.end_date, self.aliases
        )


def validate_crypto_data_signature():
    """Reject any change that breaks the legacy positional CryptoData contract."""
    parameters = list(inspect.signature(CryptoData.__init__).parameters.values())[1:]
    positional = [
        parameter.name
        for parameter in parameters
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    expected = get_rule("compatibility.legacy_fields_must_remain")
    keyword_only = [
        parameter.name
        for parameter in parameters
        if parameter.kind == inspect.Parameter.KEYWORD_ONLY
    ]
    if (
        positional != expected
        or keyword_only != get_rule("compatibility.new_kw_only_fields")
        or len(positional) != get_rule("compatibility.maximum_positional_arity")
    ):
        raise SpecIntegrityError(
            "CryptoData legacy signature drift; "
            + get_rule("compatibility.crypto_data_legacy_signature")
        )


validate_crypto_data_signature()


class CmcScraper(CryptoData):
    """Scrape cryptocurrency historical market price data from CoinMarketCap."""

    def __init__(
        self,
        coin_code=None,
        start_date=None,
        end_date=None,
        all_time=False,
        order_ascending=False,
        fiat=get_rule("fiat_ids.default_listing_convert_code"),
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
        self.headers = list(canonical_headers())
        self.rows = []
        self.id_number = id_number
        self.aliases = ()
        self.source_decision = {}

        if not (self.start_date and self.end_date):
            self.all_time = True

        if not (self.all_time or (self.start_date and self.end_date)):
            raise InvalidParameters(
                "'start_date' or 'end_date' cannot be empty if 'all_time' flag is False"
            )

    def __repr__(self):
        return "<CmcScraper coin_code:{}, start_date:{}, end_date:{}, all_time:{}>".format(
            self.coin_code, self.start_date, self.end_date, self.all_time
        )

    def _download_data(self, **kwargs):
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
        )
        source = coin_data.get("_cryptocmd", {}).get("source")
        table = parse_quote_table(coin_data, source)
        self.headers = list(table.headers)
        self.rows = [list(row) for row in table.rows]
        self.aliases = tuple(coin_data.get("_cryptocmd", {}).get("aliases", ()))
        self.source_decision = coin_data.get("_cryptocmd", {})

        if self.rows:
            self.end_date, self.start_date = self.rows[0][0], self.rows[-1][0]

        if self.order_ascending:
            self.rows.sort(
                key=lambda row: parse_spec_date(row[0]).date()
            )

    def get_data(self, format="", verbose=False, **kwargs):
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
        return self.headers, self.rows

    def get_dataframe(self, date_as_index=False, **kwargs):
        try:
            import pandas as pd
        except ImportError:
            pd = None

        if pd is None:
            raise NotImplementedError(
                "DataFrame Format requires 'pandas' to be installed. Try : pip install pandas"
            )

        self._download_data(**kwargs)
        dataframe = pd.DataFrame(data=self.rows, columns=self.headers)
        date_format = get_rule("time.date_output_format")
        dataframe["Date"] = pd.to_datetime(
            dataframe["Date"], format=date_format, dayfirst=date_format.startswith("%d")
        )
        if date_as_index:
            dataframe.set_index("Date", inplace=True)
        return dataframe

    def export_csv(self, csv_name=None, csv_path=None, **kwargs):
        warnings.warn(
            "export_csv will be deprecated; Use 'export' method instead, e.g. export('csv')",
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
                writer = csv.writer(csvfile, delimiter=",", quoting=csv.QUOTE_NONNUMERIC)
                writer.writerow(self.headers)
                for data in self.rows:
                    writer.writerow(data)
        except IOError as err:
            errno, strerror = err.args
            print("I/O error({0}): {1}".format(errno, strerror))

    def export(self, format, name=None, path=None, **kwargs):
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
            with open(_file, "wb") as output:
                if isinstance(data, str):
                    output.write(data.encode("utf-8"))
                else:
                    output.write(data)
        except IOError as err:
            errno, strerror = err.args
            print("I/O error({0}): {1}".format(errno, strerror))
        except Exception as err:
            print("format: {0}, Error: {1}".format(format, err))


