# Codex 量化机器人工作流

一套默认只做研究与模拟盘的量化工程骨架，覆盖数据清洗、无未来函数因子、信号生成、含成本回测、风控熔断、审计留痕和 HTML 报告。它不连接实盘下单接口，也不承诺收益。

## 快速开始

```powershell
python -m pip install -e .
quantbot run --config configs/default.toml
quantbot test
```

没有外部行情时，默认使用固定随机种子的合成数据。真实研究可将长表 CSV 放到 `data/raw/market.csv`，字段为 `date,symbol,open,high,low,close,volume`，并把配置中的 `source` 改为 `csv`。

每次运行会创建 `artifacts/<run_id>/`，保存配置快照、数据指纹、因子、目标仓位、净值、交易成本、指标与报告。每日模拟任务入口：

```powershell
powershell -ExecutionPolicy Bypass -File automation/daily-paper.ps1
```

详见 `docs/architecture.md`。完成样本外、滚动窗口、极端行情和模拟盘验证之前，请勿扩展为实盘执行。

## Windows 桌面客户端

运行 `build-exe.ps1` 可重新构建单文件程序。成品位于 `dist/CodexQuantBot.exe`；首次启动会在用户的 `Documents/QuantBotWorkspace` 创建可编辑配置和运行产物。客户端使用 Windows 原生窗口，不依赖浏览器或本地 HTTP 端口，并提供一键模拟研究、指标查看及报告/工作目录入口。

## OKX 模拟盘

接口固定为 Demo Trading，仅允许 `ETH-USDT-SWAP` 全仓模式。请在 OKX 模拟交易页面创建模拟盘 API Key。桌面客户端可在密码掩码输入框中载入凭据，凭据只保存在当前进程内存，关闭软件即清除；命令行仍可通过环境变量 `OKX_API_KEY`、`OKX_SECRET_KEY`、`OKX_PASSPHRASE` 注入。切勿写入 TOML 或源码。连接检查与设置配置中的 100 倍杠杆：

```powershell
quantbot okx check
quantbot okx leverage
```

模拟订单默认锁定；即使解锁也强制单笔合约数上限、止损价和确认词。实际可用杠杆由 OKX 账户层级与平台风险限制决定。
