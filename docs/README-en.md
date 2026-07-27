# deep-alpha

[简体中文](../README.md) | [English](README-en.md)

`deep-alpha` is a command-line tool for downloading, updating, and querying offline Qlib market data.

Currently supported datasets:

- China A-share daily market data: `qlib_bin.tar.gz`
- Daily indicators (daily-basic): `daily_basic_qlib_features.tar.gz`

The datasets are published through GitHub Releases in [`xu-duqing/investment_data`](https://github.com/xu-duqing/investment_data/releases) and installed into one local Qlib provider directory. The default location is:

```text
~/.qlib/qlib_data/cn_data
```

## Features

- Download and install offline Qlib data from GitHub Releases.
- Install or update the daily-basic increment without replacing calendars, instruments, or market fields.
- Update local data when a newer trading-day Release is available.
- Query historical daily K-line data and daily-basic indicators.
- Normalize symbols such as `SH600519`, `600519.SH`, and `600519` to a canonical Qlib symbol.
- Output query results as JSON, CSV, or a table.
- Remain extensible for future US daily equities, ETFs, liquidity indicators, and other offline datasets.

## Requirements

- Python 3.10 or newer
- Microsoft Qlib is required for K-line and indicator queries; install the `qlib` optional dependency
- Public Releases can be downloaded without authentication; `GITHUB_TOKEN` or `GH_TOKEN` may be used for restricted resources or a higher API rate limit

## Installation

### Full installation (recommended)

Clone the repository and install the Qlib query dependency:

```bash
git clone https://github.com/xu-duqing/deep-alpha.git
cd deep-alpha
python -m pip install -e '.[qlib]'
```

Verify the installation:

```bash
deep-alpha --version
deep-alpha --help
```

### Download and management commands only

If you only need to download, update, or inspect offline data files, install the base package without Qlib:

```bash
python -m pip install -e .
```

This does not support the `kline` or `indicator` queries. Add Qlib later with:

```bash
python -m pip install -e '.[qlib]'
```

## Quick start

### 1. Download daily market data

Install the base market dataset first:

```bash
deep-alpha download
```

Data is written to `~/.qlib/qlib_data/cn_data` by default. To use another directory:

```bash
deep-alpha download --target-dir /path/to/cn_data
```

You can also configure the provider directory for every command through an environment variable:

```bash
export DEEP_ALPHA_PROVIDER_URI=/path/to/cn_data
deep-alpha info
```

### 2. Download daily indicators

After installing the market dataset, install the daily-basic increment:

```bash
deep-alpha download --dataset daily-basic
```

The increment is calendar-fingerprint checked and safely merged into `cn_data/features/`. Existing calendars, instruments, and market fields are preserved, and unknown feature collisions are rejected.

### 3. Inspect local data

```bash
deep-alpha info
```

The output includes the provider path, trading-date range, instrument count, available fields, installed data sources, and instrument-universe information.

### 4. Query K-line data

```bash
deep-alpha kline --symbol SH600519 --start 2024-01-01 --end 2024-01-10
deep-alpha kline --symbol 600519.SH --start 2024-01-01 --end 2024-01-10
deep-alpha kline --symbol 600519 --fields open,high,low,close,volume
```

JSON is the default output format. When `--start` is omitted, the start date defaults to one calendar month before today.

Use `--format` to choose another representation:

```bash
deep-alpha kline --symbol 600519 --format table
deep-alpha kline --symbol 600519 --format csv
deep-alpha kline --symbol 600519 --format csv --output kline.csv
```

Prices are returned in original, unadjusted form by default (`--adjust none`):

```bash
# Forward-adjusted, anchored to the latest local factor
deep-alpha kline --symbol 600519 --adjust qfq

# Backward-adjusted, anchored to the earliest local factor
deep-alpha kline --symbol 600519 --adjust hfq
```

`volume` and `amount` remain original in every adjustment mode.

### 5. Query daily indicators

```bash
deep-alpha indicator --symbol 000001.SZ
deep-alpha indicator --symbol 000001.SZ --start 2026-07-01 --end 2026-07-21
deep-alpha indicator --symbol 000001.SZ --fields turnover_rate,pe,pb
deep-alpha indicator --symbol 000001.SZ --format csv --output indicators.csv
```

Without `--fields`, the command returns every daily-basic field. Its default date range is also the most recent calendar month. `indicator` accepts only daily-basic fields; use `kline` for market fields such as `close`.

Available daily-basic fields:

- Turnover and volume: `turnover_rate`, `turnover_rate_f`, `volume_ratio`
- Valuation: `pe`, `pe_ttm`, `pb`, `ps`, `ps_ttm`
- Dividend: `dv_ratio`, `dv_ttm`
- Shares: `total_share`, `float_share`, `free_share`
- Market value: `total_mv`, `circ_mv`
- Price-limit status: `limit_status`

Values retain the source archive's original units and are not adjusted or normalized. Missing values remain null/NaN.

## Updating data

Update the market dataset:

```bash
deep-alpha update
```

Update the daily-basic indicators:

```bash
deep-alpha update --dataset daily-basic
```

The tool compares local installation metadata with the selected Release and reports when the installation is already current. For daily-basic updates, `--force` only replaces files recorded as belonging to the previously installed increment; it does not clear the entire provider.

To install a specific Release, pass its tag:

```bash
deep-alpha download --tag <release-tag>
deep-alpha download --dataset daily-basic --tag <release-tag>
```

## Other useful commands

List or filter local symbols:

```bash
deep-alpha symbols
deep-alpha symbols --prefix SH
deep-alpha symbols --contains 600519
```

Inspect the trading calendar:

```bash
deep-alpha calendar
deep-alpha calendar --start 2026-07-01 --end 2026-07-31
```

Display all options for a subcommand:

```bash
deep-alpha download --help
deep-alpha kline --help
deep-alpha indicator --help
```

## Global configuration

| Setting | Description |
| --- | --- |
| `DEEP_ALPHA_PROVIDER_URI` | Override the default Qlib provider directory |
| `GITHUB_TOKEN` / `GH_TOKEN` | Optional GitHub authentication token |
| `--provider-uri` | Set the provider directory for the current command |
| `--verbose` | Enable detailed logs |

Downloads emit lifecycle logs and byte/percentage progress information to stderr.

## Development and testing

Install the test dependency and run the test suite:

```bash
python -m pip install -e '.[test]'
python -m pytest
```

To test real Qlib query functionality as well:

```bash
python -m pip install -e '.[qlib,test]'
python -m pytest
```

## Documentation

- [中文 README](../README.md)
- [Documentation index](README.md)
- [Offline data CLI technical design](offline_data_cli_design.md)
