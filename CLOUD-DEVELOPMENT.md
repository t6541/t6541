# 云端开发交接

GitHub 仓库：t6541/t6541。导入基线：CodexQuantBot-v0.7.393-Source-Handoff.zip；核心源码位于 src/quantbot，离线测试位于 tests。

## 开发边界

本阶段只进行离线源码开发与测试，不连接交易所、不启动桌面程序或自动实盘。GitHub 同步属于源码管理。禁止提交交易凭据、运行数据库、日志和构建缓存。新功能需求尚待用户提供；此次导入未修改交易逻辑。

## 环境与验证

使用 Python 3.12。项目依赖 numpy、pandas；定向测试还需要 pytest。交接包未提供离线依赖包；当前机器缺少 pytest。需要联网安装依赖时，先确认用户允许依赖下载；这不授权连接交易所。

依赖已备齐后，在仓库根目录运行：

```sh
python -m pytest -q tests/test_account05_signals.py tests/test_account05_state.py
```

迁移检查只直接执行了信号文件中不需 pytest 夹具的 12 个用例，全部通过；18 个含夹具的信号用例未运行，状态测试未运行。该结果不是完整 pytest 验证。完整回归、桌面行为、实盘行为和 Windows EXE 均未验证。

## 待处理事项

- APP_VERSION 和当前 spec 名称为 0.7.393。
- pyproject.toml 项目版本为 0.7.338。
- version_info.txt 数字 filevers/prodvers 为 0.7.390，FileVersion 为 0.7.393，ProductVersion 为 0.7.362。
- README.md 和 configs/update-manifest.json 包含历史说明，不能据此断言当前交易能力或最新发布版本。
- 包内无 automatic-fault.log，无法复查旧电脑最新实盘故障。
- 收到明确的新需求后再改代码、更新版本并测试；此次不生成新版本。

## Windows 构建

在 Windows 64 位 Python 3.12 环境准备构建依赖，更新各处版本后运行 build-exe.ps1。该脚本执行 PyInstaller，不启动 EXE。当前云主机为 Linux，不能使用此处原生 PyInstaller 生成可验证的 Windows EXE。构建后记录 SHA-256，不启动实盘。
