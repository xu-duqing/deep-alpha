# deep-alpha docs

This directory contains design and implementation documentation for the `deep-alpha` CLI.

## Documents

- [Offline data CLI technical design](offline_data_cli_design.md)

## Key constraints

- Command name: `deep-alpha`.
- Default Qlib provider directory: `~/.qlib/qlib_data/cn_data`.
- Offline data source: `https://github.com/xu-duqing/investment_data/releases`.
- Release `latest` points to the latest available trading day.
- Same-day multiple Releases are not supported or expected.
- Download resume is not supported.
- Symbol inputs such as `SH600519`, `600519.SH`, and `600519` must resolve to canonical Qlib symbols such as `SH600519`.
