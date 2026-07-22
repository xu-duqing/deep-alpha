# deep-alpha

`deep-alpha` is a CLI for downloading, updating, and querying offline market data from Qlib binary datasets.

The supported datasets are China A-share daily bars (`qlib_bin.tar.gz`) and
daily indicators (`daily_basic_qlib_features.tar.gz`) published by
`xu-duqing/investment_data` GitHub Releases. Both are read from one local Qlib
provider directory:

```text
~/.qlib/qlib_data/cn_data
```

## Capabilities

- Download offline Qlib data from `https://github.com/xu-duqing/investment_data/releases`.
- Install/update the daily-basic feature increment without replacing calendars,
  instruments, or market fields.
- Update local data when the latest Release points to a newer trading day.
- Query historical daily K-line data through Qlib.
- Normalize symbol inputs such as `SH600519`, `600519.SH`, and `600519` to a canonical symbol like `SH600519`.
- Keep the CLI extensible for future US daily equities, ETF data, liquidity indicators, and additional datasets.

## Example interface

```bash
deep-alpha download
deep-alpha download --dataset daily-basic
deep-alpha update
deep-alpha update --dataset daily-basic
deep-alpha info
deep-alpha kline --symbol SH600519 --start 2024-01-01 --end 2024-01-10
deep-alpha kline --symbol 600519.SH --start 2024-01-01 --end 2024-01-10
deep-alpha kline --symbol 600519 --start 2024-01-01 --end 2024-01-10
deep-alpha kline --symbol 600519 --fields pe,pb,total_mv,limit_status
deep-alpha indicator --symbol 000001.SZ
deep-alpha indicator --symbol 000001.SZ --fields turnover_rate,pe,pb
```

Install the market dataset first, then install the daily-basic increment. The
increment is calendar-fingerprint checked and merged into `cn_data/features/`;
unknown feature collisions are rejected. `--force` on a daily-basic update only
replaces files recorded as belonging to the previously installed increment.

Daily-basic query fields:

`turnover_rate`, `turnover_rate_f`, `volume_ratio`, `pe`, `pe_ttm`, `pb`,
`ps`, `ps_ttm`, `dv_ratio`, `dv_ttm`, `total_share`, `float_share`,
`free_share`, `total_mv`, `circ_mv`, and `limit_status`.

Values keep the source archive's original units and are not price-adjusted or
normalized. Missing values remain null/NaN.

Use `indicator` for daily indicators. Without `--fields` it returns every
daily-basic field and defaults to one calendar month of history:

```bash
deep-alpha indicator --symbol 000001.SZ
deep-alpha indicator --symbol 000001.SZ --start 2026-07-01 --end 2026-07-21
deep-alpha indicator --symbol 000001.SZ --fields turnover_rate,turnover_rate_f,volume_ratio
```

The command accepts only daily-basic fields, so market fields such as `close`
must continue to use `kline`.

K-line queries output JSON by default. Use `--format csv` or `--format table`
when another representation is preferred. If `--start` is omitted, it defaults
to one calendar month before today.

Prices and volume are returned in original, unadjusted form by default
(`--adjust none`). Use `--adjust qfq` for forward-adjusted data anchored to the
latest local factor, or `--adjust hfq` for backward-adjusted data anchored to
the earliest local factor. `volume` and `amount` remain original in every mode.

`deep-alpha info` reports installed instrument universes, instrument counts,
available fields, and each universe's data date range. Downloads emit lifecycle
logs and a byte/percentage progress bar to stderr.

## Documentation

- [Offline data CLI technical design](docs/offline_data_cli_design.md)
- [Docs index](docs/README.md)

## Installation

```bash
python -m pip install -e .
# Query commands additionally require Microsoft Qlib:
python -m pip install -e '.[qlib]'
```

Run `deep-alpha --help` for all options. `DEEP_ALPHA_PROVIDER_URI` can override
the default provider directory. GitHub authentication is optional via
`GITHUB_TOKEN` or `GH_TOKEN`.
