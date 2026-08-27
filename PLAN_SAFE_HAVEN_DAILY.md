# SPY 尾部保护每日提醒器

该程序每天读取 SPY 或 QQQ 行情和本地持仓状态，生成操作清单并保存报告。它不会连接券商或自动下单。

## 首次配置

编辑 `safe_haven_portfolio.json`，将示例数字替换为账户真实市值：

- `spy_value`：SPY 当前市值
- `sgov_value`：SGOV 当前市值
- `cash`：策略可用现金
- `puts`：每批 SPY Put 的到期日和当前市值，例如 `{"expiration": "2027-06-18", "market_value": 850.0}`
- `year_start_equity`：本年度第一个交易日的策略账户权益
- `premium_spent_ytd`：本年度已经支付的 Put 保费
- `premium_year`：保费统计年份
- `last_put_purchase`：最近一次买 Put 的日期；从未购买则为 `null`
- `completed_drawdown_stage`：已经实际执行的回撤档位，未执行为 `0`，15%/25%/35% 档分别为 `1`/`2`/`3`

市值和执行记录不会自动同步。每次成交后更新该文件，否则程序会继续提醒尚未确认执行的动作。

## 手动运行

当前 VS Code 工作区使用 Miniconda。PowerShell 中先设置：

```powershell
$conda = "C:/Users/siyaojiang/AppData/Local/miniconda3/Scripts/conda.exe"
$envPath = "C:/Users/siyaojiang/AppData/Local/miniconda3"
& $conda run -p $envPath --no-capture-output python main_safe_haven_daily.py --symbol SPY
```

报告保存到 `logs/safe_haven/YYYY-MM-DD.txt`。加上 `--notify` 会尝试通过 Windows `msg` 显示提醒：

```powershell
& $conda run -p $envPath --no-capture-output python main_safe_haven_daily.py --notify
```

## 安装每日提醒

确认持仓 JSON 已替换为真实数据后，以本地时间安装周一至周五的 Windows 计划任务：

```powershell
& $conda run -p $envPath --no-capture-output python main_safe_haven_daily.py --symbol QQQ --install-task --task-time 18:00
```

任务名按标的分别为 `SafeHavenDailyReminder_SPY` 或 `SafeHavenDailyReminder_QQQ`。删除 QQQ 任务：

```powershell
schtasks /Delete /TN SafeHavenDailyReminder_QQQ /F
```

## 每日规则

1. SPY 回撤达到 15%、25%、35% 时，分别提示兑现 Put 并逐步补回 SPY。
2. SPY 权重低于 80% 或高于 90% 时，提示恢复到 85%。
3. 距上次购买满 90 天时，在季度 0.75%、年度 3% 保费上限内提示购买 9–15 个月、虚值 25%–35% 的 Put。
4. Put 剩余期限不超过 180 天时提示检查并滚动。
5. 没有规则触发时明确输出“不交易”。

期权建议只给预算、期限和大致执行价范围。下单前仍需检查实时买卖价差、成交量、税务和合约乘数。