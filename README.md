# deep-alpha

`deep-alpha` is a CLI for downloading, updating, and querying offline market data from Qlib binary datasets.

The first dataset target is China A-share daily bars published by `xu-duqing/investment_data` GitHub Releases. The CLI reads local data through the Qlib Python library from the default provider directory:

```text
~/.qlib/qlib_data/cn_data
```

## Capabilities

- Download offline Qlib data from `https://github.com/xu-duqing/investment_data/releases`.
- Update local data when the latest Release points to a newer trading day.
- Query historical daily K-line data through Qlib.
- Normalize symbol inputs such as `SH600519`, `600519.SH`, and `600519` to a canonical symbol like `SH600519`.
- Keep the CLI extensible for future US daily equities, ETF data, liquidity indicators, and additional datasets.

## Example interface

```bash
deep-alpha download
deep-alpha update
deep-alpha info
deep-alpha kline --symbol SH600519 --start 2024-01-01 --end 2024-01-10
deep-alpha kline --symbol 600519.SH --start 2024-01-01 --end 2024-01-10
deep-alpha kline --symbol 600519 --start 2024-01-01 --end 2024-01-10
```

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
