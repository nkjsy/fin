# 上涨熔断日内动量策略：每日前向测试

本文说明 `main_halt_momentum_forward.py` 的使用方式。脚本在美股收盘后下载目标交易日的一分钟行情，对全市场执行上涨熔断缺口代理检测，并生成当日模拟交易总结。

> 重要：脚本从 Schwab 一分钟 OHLCV 的缺口推断疑似熔断，不使用官方 LULD 事件记录，因此结果必须标记为 `OHLCV_GAP_PROXY`，不能视为已确认的官方熔断。

## 策略规则

### 股票池

- 默认从 Nasdaq 股票筛选接口获取全部可用美股代码，目前约 7,000 只。
- 只对复牌分钟开盘价严格大于 `$2.00` 的事件交易。
- `--symbols` 可用于仅测试指定代码。

### 疑似上涨熔断条件

仅检查美东时间常规交易时段 `09:30-16:00`：

1. 连续缺少至少 5 根一分钟K线。
2. 缺口前 5 根一分钟K线必须连续存在。
3. 缺口前 5 分钟从第一根开盘价至最后一根收盘价上涨至少 10%。
4. 恢复交易第一根K线的开盘价必须大于 `$2.00`。

### 买卖规则

- 买入：恢复交易第一根一分钟K线的开盘价。
- 止盈：买入价上涨 10%。
- 止损：买入价下跌 10%。
- 时间退出：入场 5 分钟后的K线开盘价。
- 同一根K线同时触及止盈和止损时，保守地按止损处理。
- 如果第 5 分钟没有成交K线，使用下一根可用K线的开盘价退出，并标记为 `TIME_NEXT_TRADE`。

退出原因：

| 原因 | 含义 |
| --- | --- |
| `TARGET` | 触及 +10% 止盈 |
| `STOP` | 触及 -10% 止损 |
| `TIME` | 第 5 分钟按开盘价退出 |
| `TIME_NEXT_TRADE` | 第 5 分钟无K线，按下一根K线开盘价退出 |
| `END_OF_DATA` | 当日后续数据不足，按最后一根K线收盘价退出 |

## 环境要求

- Windows
- 已配置并可正常认证的 Schwab 客户端
- 项目依赖已安装：

```powershell
pip install -r requirements.txt
```

当前项目使用的 Conda Python 命令前缀为：

```powershell
C:/Users/siyaojiang/AppData/Local/miniconda3/Scripts/conda.exe run -p C:\Users\siyaojiang\AppData\Local\miniconda3 --no-capture-output python
```

## 每日运行

在美股收盘后执行：

```powershell
python main_halt_momentum_forward.py
```

默认行为：

- 使用当前美东日期。
- 查询 Schwab 市场日历，确认当天开市且已经收盘。
- 扫描全部美股代码。
- 输出到 `logs/halt_momentum_forward/YYYY-MM-DD/`。

使用本项目 Conda 环境：

```powershell
C:/Users/siyaojiang/AppData/Local/miniconda3/Scripts/conda.exe run -p C:\Users\siyaojiang\AppData\Local\miniconda3 --no-capture-output python main_halt_momentum_forward.py
```

全市场需要逐只请求 Schwab，可能运行数小时。目标日期在程序启动时固定，跨过午夜也不会混入下一交易日。

## 常用参数

指定日期：

```powershell
python main_halt_momentum_forward.py --date 2026-08-25
```

测试少量股票：

```powershell
python main_halt_momentum_forward.py --date 2026-08-25 --symbols AAPL MSFT NVDA
```

节假日、休市日或收盘前强制运行：

```powershell
python main_halt_momentum_forward.py --date 2026-08-25 --force
```

指定输出根目录：

```powershell
python main_halt_momentum_forward.py --output logs/my_forward_results
```

调整进度打印频率：

```powershell
python main_halt_momentum_forward.py --progress-every 25
```

查看全部参数：

```powershell
python main_halt_momentum_forward.py --help
```

## 断点续跑

每处理一只股票，脚本立即向 `progress.csv` 写入结果。运行中断后，执行完全相同的命令即可续跑：

```powershell
python main_halt_momentum_forward.py
```

状态为 `OK` 的股票会被跳过。`NO_DATA` 股票会在下次运行时重试。连续 10 个代码无数据时，程序会用 AAPL 检查连接；如果 Schwab 不可用，程序停止并保留已有进度。

不要在同一个日期目录中途更换股票池，否则汇总中的请求数可能与已有进度不一致。需要重新扫描时，应使用新的 `--output` 目录或先人工归档原日期目录。

## Windows 定时任务

安装工作日定时任务：

```powershell
python main_halt_momentum_forward.py --install-task --task-time 16:15
```

任务名称为 `HaltMomentumForwardDaily`，时间使用 Windows 本地时区。请根据电脑所在时区设置为美股收盘之后；例如电脑使用美东时区时可设置 `16:15`。

此命令只按周一至周五触发。脚本仍会查询 Schwab 市场日历，因此美股节假日不会误生成正常交易日报。

## 输出文件

每日目录：

```text
logs/halt_momentum_forward/YYYY-MM-DD/
├── progress.csv
├── events.csv
├── trades.csv
├── summary.csv
└── summary.txt
```

- `progress.csv`：每只股票的处理状态、K线数量、事件数和交易数。
- `events.csv`：检测到的疑似熔断事件。当天没有事件时可能不存在。
- `trades.csv`：模拟交易明细。当天没有交易时可能不存在。
- `summary.csv`：机器可读的当日统计。
- `summary.txt`：便于阅读的当日总结和交易清单。

`summary.csv` 的严格统计字段以 `Strict` 开头，会排除 `TIME_NEXT_TRADE`。原因是延迟到下一根K线成交并不等同于严格的 5 分钟退出。

## 验证

运行合成数据测试：

```powershell
python test_halt_momentum_forward.py
```

测试覆盖：

- 疑似熔断事件检测。
- 第 5 分钟退出。
- 当日报告写入。
- 同日断点续跑。
- 严格统计排除 `TIME_NEXT_TRADE`。

## 已知限制

1. OHLCV 缺口也可能由极低流动性、停牌或数据缺失造成，并不一定是上涨 LULD 熔断。
2. 一分钟K线无法确定分钟内价格路径；同一分钟同时触及止盈和止损时只能采用保守假设。
3. 买入和退出使用K线价格模拟，不包含滑点、手续费、点差、排队、拒单和成交量约束。
4. 脚本在收盘后运行，只做每日前向记录和模拟，不会发送真实订单。
5. Nasdaq 返回的股票池和 Schwab 可查询代码可能不完全一致，部分代码会记录为 `NO_DATA`。
6. 汇总中的复合收益是假设交易按顺序使用全部资金计算，不代表可实际实现的组合收益。
