# CodexQuantBot 新电脑开发交接文档

## 当前版本

- 源码基线：`v0.7.393`
- 当前隔离目录：`H:\codex实盘交易软件开发\CodexQuantBot-Live-Development`
- 本次最新离线构建：`dist\CodexQuantBot-v0.7.393-Slots-Base-Switch.exe`
- 本交接包不包含 API key、secret、passphrase、Cookie、运行数据库、日志和本机凭据。

## 迁移包内容

- `src\quantbot\`：核心策略、账户05执行、状态账本、OKX适配和桌面界面。
- `tests\`：离线单元测试与回归测试。
- `configs\`：非敏感配置模板。
- `desktop_entry.py`、`pyproject.toml`、`quantbot_v096.spec`、`version_info.txt`、`build-exe.ps1`：入口、依赖和构建文件。
- `README.md`、`CHANGELOG.md`：项目说明和版本记录。

## 新电脑准备

1. 将压缩包解压到新电脑的开发目录，建议使用 `H:` 盘。
2. 使用 Python 3.12（Windows 64 位）。
3. 安装项目依赖；若使用仓库内 `.build-deps`，不要把它当作源码修改。
4. 在本机创建账户配置和凭据文件。不要把凭据写入 Git、压缩包或聊天记录。
5. 先运行离线测试，再进行任何实盘启动操作。

## 离线验证

在项目根目录执行：

```powershell
$env:PYTHONPATH=(Resolve-Path '.\\src').Path+';'+(Resolve-Path '.\\.test-deps').Path
& 'C:\\Path\\To\\python.exe' -m pytest -q tests/test_account05_signals.py tests/test_account05_state.py
```

若 Windows 临时目录权限导致 pytest 在结束清理阶段报 `PermissionError`，先记录实际用例结果；不要因此删除源码或数据库。

## 重新构建 EXE

```powershell
& .\\build-exe.ps1
```

构建前同步更新：

- `src\\quantbot\\version.py` 的 `APP_VERSION`
- `version_info.txt`
- `quantbot_v096.spec` 的 EXE 名称

构建后用以下命令核验：

```powershell
Get-FileHash .\\dist\\CodexQuantBot-v<版本>-Slots-Base-Switch.exe -Algorithm SHA256
```

## 当前功能重点

- 账户05独立 SQLite 账本和自动执行路径。
- 1分钟/5分钟超级趋势底部翻多、支撑回踩追多、底部反转三阶段信号。
- 1分钟上涨超级趋势有效时暂缓逆势空单；1分钟和5分钟同时翻多时提供双周期确认。
- 解套利润池、槽位和基础仓逻辑均在账户05执行层维护。
- 手工订单窗口从 OKX 订单历史和成交历史读取，支持平仓时间、平仓价和订单号回填；窗口可最大化。
- 自动执行故障会写入 `automatic-fault.log`。继续开发时先读取最新故障堆栈，再改代码。

## 重要边界

- 迁移、测试和构建默认离线进行。
- 不要在未获得当轮明确授权时启动 EXE、重启自动交易、下单、撤单、改单、平仓或修改杠杆。
- 不要把旧电脑的运行数据库直接覆盖新电脑数据库；需要保留历史时先复制成只读备份。

## 新对话开场指令

> 我已收到 `CodexQuantBot-v0.7.393-Source-Handoff.zip`。请先读取 `NEW-COMPUTER-HANDOFF-v0.7.393.md`，确认当前版本、源码目录和未验证项目；只在隔离目录离线检查，不连接交易所、不启动实盘。先运行账户05定向测试，再根据我新的需求修改并重新打包最新版 EXE。
