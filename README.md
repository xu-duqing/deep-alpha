# deep-alpha

[简体中文](README.md) | [English](docs/README-en.md)

## 别再把时间浪费在下载、转换和修补 Qlib 数据上

想用 Qlib 研究 A 股，真正挡在策略前面的往往不是模型，而是数据：

- 行情接口有 Token、积分、限频和网络依赖；
- 原始数据还要清洗、转换成 Qlib binary，再维护交易日历和标的列表；
- 每日更新容易变成一套脆弱的脚本，失败后还不知道本地究竟更新到了哪天；
- 估值、换手率、市值等指标来自另一张表，合并时一不小心就会覆盖已有行情或错位交易日；
- 换一台电脑、重装环境或交付给同事，又要从头搭一遍。

`deep-alpha` 把这些重复工作收进一个命令行工具：从 GitHub Releases 下载已经转换好的 A 股 Qlib 数据，安全安装到本地，并提供更新、检查和查询命令。

**下载一次，本地使用；一条命令更新，不再自己维护行情入库流水线。**

```text
GitHub Release 数据包
        │
        ▼
deep-alpha：下载 → 校验 → 安装/安全合并 → 版本记录
        │
        ▼
~/.qlib/qlib_data/cn_data
        │
        ├── Qlib 研究与回测
        └── deep-alpha 查询 / 导出
```

## 它解决什么问题

| 你原本要处理的事 | deep-alpha 提供的能力 |
| --- | --- |
| 自己采集行情、整理字段、转换 Qlib binary | 直接下载可用的离线 Qlib provider |
| 手工判断数据是否过期、整包替换 | `deep-alpha update` 对比 Release 版本并更新 |
| 数据持续更新后，担心研究代码、字段路径或查询方式跟着变化 | 数据仍安装到同一个 Qlib provider，研究代码和查询接口保持不变 |
| 更新中途失败，担心当前可用数据也被破坏 | 完整行情先在临时目录校验再替换；daily-basic 安装失败自动回滚本次改动 |
| 将估值、市值、换手率等字段并入行情库 | daily-basic 增量包按同一交易日历安全合并 |
| 担心增量更新破坏日历、股票池或已有字段 | 校验日历指纹、限制字段范围、拒绝未知冲突 |
| 不清楚本地覆盖到哪天、有哪些股票和字段 | `info`、`calendar`、`symbols` 直接检查 |
| 为一次数据核验专门写 Qlib/Pandas 脚本 | `kline`、`indicator` 直接查询并导出 JSON/CSV/表格 |

数据安装完成后，查询和回测读取本机文件，不再受远程行情接口的限频、Token 或临时网络故障影响。上游数据继续更新时，本地 provider 路径、Qlib 读取方式和研究代码都不需要跟着改变；即使上游暂时不可用，已经下载的数据仍可继续离线查询和回测。

## 当前可用数据

| 数据集 | Release 资产 | 内容 |
| --- | --- | --- |
| A 股日线行情 | `qlib_bin.tar.gz` | OHLC、成交量、成交额、VWAP、复权因子等 |
| 每日指标（daily-basic） | `daily_basic_qlib_features.tar.gz` | 换手率、量比、估值、股息、股本、市值、涨跌停状态 |

数据由 [`xu-duqing/investment_data`](https://github.com/xu-duqing/investment_data/releases) 发布，默认安装到：

```text
~/.qlib/qlib_data/cn_data
```

> `deep-alpha` 当前定位是 A 股日线离线数据的安装、更新和查询工具，不提供实时行情、分钟线、GUI 或在线交易接口。

## 三分钟开始

### 1. 安装

```bash
git clone https://github.com/xu-duqing/deep-alpha.git
cd deep-alpha
python -m pip install -e '.[qlib]'
```

`qlib` 是查询依赖。如果只想下载、更新和检查本地数据，可执行 `python -m pip install -e .`。

### 2. 下载行情与每日指标

```bash
# 安装基础日线行情
deep-alpha download

# 在同一 provider 中安全合并 daily-basic 指标
deep-alpha download --dataset daily-basic
```

### 3. 确认数据是否可用

```bash
deep-alpha info
deep-alpha calendar
deep-alpha symbols --contains 600519
```

`info` 会显示本地目录、交易日起止、证券数量、可用字段，以及 market 和 daily-basic 各自的安装版本。

### 4. 直接查询

```bash
# 日 K 线；兼容 SH600519、600519.SH、600519
deep-alpha kline --symbol 600519 --start 2024-01-01 --end 2024-01-10

# 估值、换手率和市值
deep-alpha indicator --symbol 000001.SZ \
  --fields turnover_rate,pe,pb,total_mv,circ_mv

# 导出 CSV
deep-alpha indicator --symbol 000001.SZ \
  --start 2026-07-01 --end 2026-07-21 \
  --format csv --output indicators.csv
```

默认输出 JSON；也支持 `--format csv` 和 `--format table`。未指定 `--start` 时默认查询最近一个自然月。

## 更新不再靠猜

```bash
# 更新基础行情
deep-alpha update

# 更新 daily-basic 指标
deep-alpha update --dataset daily-basic

# 查看本地数据实际更新到哪一个交易日
deep-alpha info
```

工具会比较本地安装元数据与目标 Release。版本相同时直接提示已经是最新状态；需要安装固定版本时可以指定标签：

```bash
deep-alpha download --tag <release-tag>
deep-alpha download --dataset daily-basic --tag <release-tag>
```

基础行情采用完整 provider 安装；daily-basic 采用增量安装，只合并它负责的指标文件。daily-basic 更新时，即使使用 `--force`，也只替换上一次增量安装记录所属的文件，不会清空整个 provider。

## 为什么 daily-basic 要单独安装

行情和每日指标的更新节奏、来源与字段归属不同。简单解压覆盖容易造成三个问题：

1. 指标 `.bin` 使用了不同交易日历，读取时日期静默错位；
2. 增量包覆盖 `calendars/` 或 `instruments/`，破坏原有 provider；
3. 新增字段与行情字段重名，旧数据被意外替换。

`deep-alpha` 在安装前校验目标 provider 和交易日历指纹，只允许 daily-basic 明确拥有的字段进入 `features/`，并记录文件清单和校验值，用于后续安全更新；安装失败时回滚本次改动。行情、交易日历、股票池及其他字段保持不变。

## 查询能力

### 日 K 线与复权

```bash
# 指定字段
deep-alpha kline --symbol 600519 \
  --fields open,high,low,close,volume,amount,vwap

# 前复权：以本地最新复权因子为锚点
deep-alpha kline --symbol 600519 --adjust qfq

# 后复权：以本地最早复权因子为锚点
deep-alpha kline --symbol 600519 --adjust hfq
```

默认 `--adjust none` 返回原始、不复权价格。无论选择哪种复权模式，`volume` 和 `amount` 都保持原始值。

### 每日指标

| 类别 | 字段 |
| --- | --- |
| 换手与量比 | `turnover_rate`、`turnover_rate_f`、`volume_ratio` |
| 估值 | `pe`、`pe_ttm`、`pb`、`ps`、`ps_ttm` |
| 股息 | `dv_ratio`、`dv_ttm` |
| 股本 | `total_share`、`float_share`、`free_share` |
| 市值 | `total_mv`、`circ_mv` |
| 交易状态 | `limit_status` |

```bash
# 查询全部 daily-basic 字段
deep-alpha indicator --symbol 000001.SZ

# 只查询需要的字段
deep-alpha indicator --symbol 000001.SZ --fields turnover_rate,pe,pb
```

指标保留数据包中的原始单位，不复权、不归一化；缺失值保持为 null/NaN。`indicator` 只接受 daily-basic 字段，`close` 等行情字段请使用 `kline`。

### 证券与交易日历

```bash
deep-alpha symbols
deep-alpha symbols --prefix SH
deep-alpha symbols --contains 600519

deep-alpha calendar
deep-alpha calendar --start 2026-07-01 --end 2026-07-31
```

## 自定义数据目录

单次指定安装目录：

```bash
deep-alpha download --target-dir /path/to/cn_data
deep-alpha info --provider-uri /path/to/cn_data
```

也可以统一设置环境变量：

```bash
export DEEP_ALPHA_PROVIDER_URI=/path/to/cn_data
deep-alpha info
```

| 配置 | 说明 |
| --- | --- |
| `DEEP_ALPHA_PROVIDER_URI` | 覆盖默认 Qlib provider 目录 |
| `GITHUB_TOKEN` / `GH_TOKEN` | 可选；用于受限资源或提高 GitHub API 限额 |
| `--provider-uri` | 为当前命令指定 provider 目录 |
| `--verbose` | 输出详细日志 |

公开 Release 无需 GitHub 登录。下载进度和生命周期日志写入标准错误流，JSON/CSV 等查询结果仍可安全通过管道处理。

## 适合谁

- 想直接开始 Qlib 因子研究，不想先搭数据 ETL 的个人研究者；
- 需要一份可重复安装、可检查版本的本地 A 股日线数据；
- 已有 Qlib provider，希望安全补充估值、换手率和市值指标；
- 希望研究脚本只依赖本地数据，不与在线行情接口耦合的团队。

如果你需要实时交易、分钟行情、任意在线数据源聚合或图形化终端，本项目目前并不适合这些场景。

## 开发与测试

```bash
python -m pip install -e '.[test]'
python -m pytest
```

同时验证真实 Qlib 查询能力：

```bash
python -m pip install -e '.[qlib,test]'
python -m pytest
```

## 文档

- [English README](docs/README-en.md)
- [文档索引](docs/README.md)
- [离线数据 CLI 技术设计](docs/offline_data_cli_design.md)

## 截图
<img width="542" height="439" alt="image" src="https://github.com/user-attachments/assets/d7463ebc-3611-429a-a51c-824b23baa5c0" />

<img width="592" height="87" alt="image" src="https://github.com/user-attachments/assets/118560ef-0966-416a-a3dd-bf5fab527f9f" />




## 数据与使用边界

本项目负责数据包的下载、安装、更新与本地读取，不对上游数据的完整性、及时性或特定用途适用性作保证。数据版权、授权及再分发条件由数据源和相关权利人决定，请在使用前自行确认。

本项目仅用于数据管理、软件学习与量化研究，不构成任何投资建议。
