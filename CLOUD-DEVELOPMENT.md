# 云端开发交接

GitHub 仓库：t6541/t6541。导入基线：CodexQuantBot-v0.7.393-Source-Handoff.zip；核心源码位于 src/quantbot，离线测试位于 tests。

## 开发边界

本阶段只进行离线源码开发与测试，不连接交易所、不启动桌面程序或自动实盘。GitHub 同步属于源码管理。禁止提交交易凭据、运行数据库、日志和构建缓存。v0.7.394完善利润池排序刷新和手工成交历史展示，未修改交易逻辑。

## 环境与验证

使用 Python 3.12。项目依赖 numpy、pandas；定向测试还需要 pytest。交接包未提供离线依赖包；用户已允许从Python官方仓库下载测试依赖，当前机器已安装pytest到 /workspace/isolated/offline-test-deps。使用该路径时设置PYTHONPATH。这不授权连接交易所。

依赖已备齐后，在仓库根目录运行：

```sh
python -m pytest -q tests/test_account05_signals.py tests/test_account05_state.py
```

迁移检查只直接执行了信号文件中不需 pytest 夹具的 12 个用例，全部通过；18 个含夹具的信号用例未运行，状态测试未运行。该结果不是完整 pytest 验证。后续已补装pytest并在屏蔽Python socket网络连接与DNS解析的运行器中完成账户05信号/状态52项测试。当前功能定向验证共75项通过，额外执行/策略回归47项通过、20项失败，与未修改0.7.393基线失败项完全相同，见docs/validation-v0.7.394.json；Windows原生界面、实盘行为和Windows EXE仍需单独验证。

## 待处理事项

- v0.7.394已同步APP_VERSION、pyproject.toml、当前spec及version_info.txt各版本字段。
- README.md 和 configs/update-manifest.json 包含历史说明，不能据此断言当前交易能力或最新发布版本。
- 包内无 automatic-fault.log，无法复查旧电脑最新实盘故障。
- 手工历史的平仓关联为按方向/数量/时间FIFO推算，交易所通常不返回开仓到平仓的唯一父子关系；不可将推算标为交易所确认。

## Windows 构建

在 Windows 64 位 Python 3.12 环境准备构建依赖，更新各处版本后运行 build-exe.ps1。该脚本执行 PyInstaller，不启动 EXE。当前云主机为 Linux，不能使用此处原生 PyInstaller 生成可验证的 Windows EXE。构建后记录 SHA-256，不启动实盘。

GitHub Actions已提供手动Windows构建流程（Build Windows EXE），尚未执行验证。当前会话GitHub API返回Forbidden，需在仓库Actions页面手动运行并下载产物。

## Windows首次构建修复

首次Actions运行37471148707在测试阶段失败：Windows默认cp1252读取UTF-8源码导致4项UnicodeDecodeError，71项通过，打包步骤未执行。已明确指定UTF-8，Linux离线75项复验通过，并模拟Windows默认cp1252读取环境验证21项通过；仍需实际Windows构建完成证明。工作流现支持main相关源码/测试/构建文件推送后自动打包，保留手动运行入口。

API诊断：云环境网络代理在HTTPS CONNECT阶段返回403，配置允许域名未包含api.github.com；已将该域名加入环境配置草稿，尚需环境设置保存/应用后再验证。GitHub自身Actions授权还未验证，不应仅凭代理拒绝访问就认定缺少GitHub权限。当前会话无用户电脑远程控制工具。

## 后续Windows构建结果

修复后的运行37472678728（源码提交746a5bf）已成功，公开GitHub汇总页面可确认Success及1个产物：CodexQuantBot-Windows-EXE，29.9 MB。此结果替代上面的未构建状态。当前会话尚未下载或启动EXE，未核验EXE本身SHA-256；公开页面的产物摘要属于上传归档而不是内部EXE。API代理域名配置仍待应用。

## 当前开发版本v0.7.395

账户05历史GET路径白名单及历史来源降级修复，99项定向离线测试通过。GitHub API读取已恢复。Windows流程现发布原始EXE到GitHub Releases，停止Actions ZIP产物上传。此段替代上面手动运行/压缩包/API不可访问的当前状态说明；历史记录仅用于追溯。最新交接见NEW-COMPUTER-HANDOFF-v0.7.395.md。

## v0.7.396

极值先平全部合格手工单/自动小单再反手，基础仓维持独立规则。新增 tests/test_extreme_profit_sweep.py，覆盖全组确认、镜像方向、可配置严格门槛、部分成交与撤单竞态、请求中断重启、利润池去重及历史累计数量修复。定向119项通过；原额外策略/执行测试47通过、20失败，未新增失败测试ID。当前交接以 NEW-COMPUTER-HANDOFF-v0.7.396.md 与 docs/validation-v0.7.396.json 为准。

## v0.7.397

修复小单限价止盈取消后被误标为缺少独立止盈导致全账户故障停止。定向129项通过；额外旧执行/策略47通过、20失败，未新增失败ID。当前交接见NEW-COMPUTER-HANDOFF-v0.7.397.md、docs/validation-v0.7.397.json。普通升级只保存源码与交接文档，不要求重复发布云环境。

## v0.7.398

用户停用基础仓新建/补仓/重建，旧开启设置与按钮无效；仅保留旧仓止盈保护至结束，不影响小单、利润池和极值反手。包含v0.7.397误停修复。当前交接见NEW-COMPUTER-HANDOFF-v0.7.398.md与docs/validation-v0.7.398.json。无需重复发布云环境。

## v0.7.399

数量不一致改为只读对账等待与自动重试；已知退出成交优先同步，自动开仓身份避免重复手工导入。定向155项离线通过。交接见NEW-COMPUTER-HANDOFF-v0.7.399.md及docs/validation-v0.7.399.json；无需重新发布云环境。

## v0.7.400

按用户新规则以实际持仓校正超额虚拟数量，空仓自动清理残留，校正不计成交/利润；持久化防复活、延迟历史防重复扣量，真实终态/竞态只按实际成交记账。定向168项离线通过；交接见NEW-COMPUTER-HANDOFF-v0.7.400.md与docs/validation-v0.7.400.json。无需再发布云环境。
