# deep-alpha

[简体中文](README.md) | [English](docs/README-en.md)

`deep-alpha` 是一个用于下载、更新和查询离线 Qlib 行情数据的命令行工具。

当前支持的数据集：

- 中国 A 股日线行情：`qlib_bin.tar.gz`
- 每日指标（daily-basic）：`daily_basic_qlib_features.tar.gz`

数据来自 [`xu-duqing/investment_data`](https://github.com/xu-duqing/investment_data/releases) 的 GitHub Releases，并统一安装到本地 Qlib provider 目录。默认目录为：

```text
~/.qlib/qlib_data/cn_data
```

## 功能特性

- 从 GitHub Releases 下载并安装离线 Qlib 数据。
- 在不替换交易日历、标的列表和行情字段的前提下，增量安装或更新 daily-basic 指标。
- 当最新 Release 包含更新的交易日数据时更新本地数据。
- 查询历史日 K 线和 daily-basic 指标。
- 支持 `SH600519`、`600519.SH`、`600519` 等证券代码格式，并统一转换为 Qlib 标准代码。
- 支持 JSON、CSV 和表格输出。
- 可扩展接入美股日线、ETF、流动性指标等其他离线数据集。

## 环境要求

- Python 3.10 或更高版本
- 查询 K 线或指标时需要 Microsoft Qlib（安装 `qlib` 可选依赖）
- 下载公开 Release 无需 GitHub 登录；访问受限资源或提高 API 限额时可配置 `GITHUB_TOKEN` 或 `GH_TOKEN`

## 安装

### 完整安装（推荐）

从源码克隆项目，并同时安装 Qlib 查询依赖：

```bash
git clone https://github.com/xu-duqing/deep-alpha.git
cd deep-alpha
python -m pip install -e '.[qlib]'
```

安装完成后检查命令：

```bash
deep-alpha --version
deep-alpha --help
```

### 仅安装下载和管理功能

如果暂时只需要下载、更新或查看离线数据文件，可以不安装 Qlib：

```bash
python -m pip install -e .
```

此方式不支持 `kline` 和 `indicator` 查询；需要查询时再执行：

```bash
python -m pip install -e '.[qlib]'
```

## 快速开始

### 1. 下载日线行情

首次使用时，先安装基础行情数据：

```bash
deep-alpha download
```

行情默认写入 `~/.qlib/qlib_data/cn_data`。如需指定目录：

```bash
deep-alpha download --target-dir /path/to/cn_data
```

也可以通过环境变量为所有命令设置 provider 目录：

```bash
export DEEP_ALPHA_PROVIDER_URI=/path/to/cn_data
deep-alpha info
```

### 2. 下载每日指标

基础行情安装完成后，再安装 daily-basic 增量包：

```bash
deep-alpha download --dataset daily-basic
```

该增量包会经过交易日历指纹校验，然后安全合并到 `cn_data/features/`。安装过程不会替换已有的交易日历、标的列表或行情字段；未知字段冲突会被拒绝。

### 3. 检查本地数据

```bash
deep-alpha info
```

输出包含 provider 路径、交易日范围、证券数量、可用字段、已安装数据源和各标的集合的信息。

### 4. 查询 K 线

```bash
deep-alpha kline --symbol SH600519 --start 2024-01-01 --end 2024-01-10
deep-alpha kline --symbol 600519.SH --start 2024-01-01 --end 2024-01-10
deep-alpha kline --symbol 600519 --fields open,high,low,close,volume
```

默认输出 JSON。如果未指定 `--start`，默认查询从当前日期往前一个自然月的数据。

可使用 `--format` 选择输出格式：

```bash
deep-alpha kline --symbol 600519 --format table
deep-alpha kline --symbol 600519 --format csv
deep-alpha kline --symbol 600519 --format csv --output kline.csv
```

价格默认返回原始、不复权数据（`--adjust none`）：

```bash
# 前复权，以本地最新复权因子为锚点
deep-alpha kline --symbol 600519 --adjust qfq

# 后复权，以本地最早复权因子为锚点
deep-alpha kline --symbol 600519 --adjust hfq
```

无论选择哪种复权模式，`volume` 和 `amount` 均保持原始值。

### 5. 查询每日指标

```bash
deep-alpha indicator --symbol 000001.SZ
deep-alpha indicator --symbol 000001.SZ --start 2026-07-01 --end 2026-07-21
deep-alpha indicator --symbol 000001.SZ --fields turnover_rate,pe,pb
deep-alpha indicator --symbol 000001.SZ --format csv --output indicators.csv
```

不传 `--fields` 时会返回全部 daily-basic 字段，默认时间范围同样是最近一个自然月。`indicator` 只接受 daily-basic 字段；`close` 等行情字段请使用 `kline` 查询。

支持的 daily-basic 字段：

- 换手与量比：`turnover_rate`、`turnover_rate_f`、`volume_ratio`
- 估值：`pe`、`pe_ttm`、`pb`、`ps`、`ps_ttm`
- 股息：`dv_ratio`、`dv_ttm`
- 股本：`total_share`、`float_share`、`free_share`
- 市值：`total_mv`、`circ_mv`
- 涨跌停状态：`limit_status`

指标值保留源数据包中的原始单位，不进行复权或归一化；缺失值保持为 null/NaN。

## 更新数据

更新基础行情：

```bash
deep-alpha update
```

更新 daily-basic 指标：

```bash
deep-alpha update --dataset daily-basic
```

工具会比较本地安装元数据与目标 Release。版本相同时会提示已经是最新状态。daily-basic 更新中的 `--force` 只替换上一次增量安装记录所属的文件，不会清空整个 provider。

需要安装指定 Release 时可传入标签：

```bash
deep-alpha download --tag <release-tag>
deep-alpha download --dataset daily-basic --tag <release-tag>
```

## 其他常用命令

列出或筛选本地证券代码：

```bash
deep-alpha symbols
deep-alpha symbols --prefix SH
deep-alpha symbols --contains 600519
```

查看交易日历：

```bash
deep-alpha calendar
deep-alpha calendar --start 2026-07-01 --end 2026-07-31
```

查看任一子命令的完整参数：

```bash
deep-alpha download --help
deep-alpha kline --help
deep-alpha indicator --help
```

## 全局配置

| 配置 | 说明 |
| --- | --- |
| `DEEP_ALPHA_PROVIDER_URI` | 覆盖默认 Qlib provider 目录 |
| `GITHUB_TOKEN` / `GH_TOKEN` | 可选的 GitHub 认证令牌 |
| `--provider-uri` | 为当前命令指定 provider 目录 |
| `--verbose` | 输出详细日志 |

下载过程中会在标准错误流显示生命周期日志、已下载字节数和百分比进度。

## 开发与测试

安装测试依赖并运行测试：

```bash
python -m pip install -e '.[test]'
python -m pytest
```

如需同时测试真实 Qlib 查询能力：

```bash
python -m pip install -e '.[qlib,test]'
python -m pytest
```

## 文档

- [English README](docs/README-en.md)
- [文档索引](docs/README.md)
- [离线数据 CLI 技术设计](docs/offline_data_cli_design.md)
