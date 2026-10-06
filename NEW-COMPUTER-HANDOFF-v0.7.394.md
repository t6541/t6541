# CodexQuantBot v0.7.394 云端开发交接

- GitHub：t6541/t6541，main 分支。源码：src/quantbot。
- 开发机工作目录：/workspace/t6541；无需另外创建Git worktree。
- 修改：利润池时间排序自动刷新对账；手工订单历史数量匹配和最多20条展示。
- 平仓订单按方向、数量、时间先进先出匹配；历史没有唯一父子关系时，该关联为推算。分批平仓显示加权价格、多个订单号及部分平仓状态；不根据空白字段断言交易所实际未平仓。
- 最近20条按最新开仓或已匹配平仓成交时间选择；仅限制显示，不删除数据库或交易所记录。
- 离线定向验证75项通过。额外执行/策略回归47项通过、20项失败；未改动的0.7.393基线有完全相同的20项失败，详见docs/validation-v0.7.394.json。
- 未连接交易所、未启动桌面/实盘。Windows原生窗口及实际历史回填效果尚未运行验证。
- Windows EXE尚未构建。GitHub Actions中提供Build Windows EXE手动构建流程：选择main，Run workflow，完成后下载CodexQuantBot-Windows-EXE构建产物，其中包含EXE和SHA256SUMS.txt。该流程未在本次环境执行验证。
- 当前GitHub API返回Forbidden，无法从云端会话代为触发/查询Actions。无需提供交易凭据或在聊天中提供GitHub令牌。
- 不得未经当轮明确授权连接交易所、启动实盘、下单/撤单/改单/平仓/修改杠杆。不要把运行凭据、数据库或日志提交到Git或放入交接包。

## Windows首次构建修复

首次Actions运行37471148707在测试阶段失败：Windows默认cp1252读取UTF-8源码导致4项UnicodeDecodeError，71项通过，打包步骤未执行。已明确指定UTF-8，Linux离线75项复验通过，并模拟Windows默认cp1252读取环境验证21项通过；仍需实际Windows构建完成证明。工作流现支持main相关源码/测试/构建文件推送后自动打包，保留手动运行入口。

API诊断：云环境网络代理在HTTPS CONNECT阶段返回403，配置允许域名未包含api.github.com；已将该域名加入环境配置草稿，尚需环境设置保存/应用后再验证。GitHub自身Actions授权还未验证，不应仅凭代理拒绝访问就认定缺少GitHub权限。当前会话无用户电脑远程控制工具。

## 后续Windows构建结果

修复后的运行37472678728（源码提交746a5bf）已成功，公开GitHub汇总页面可确认Success及1个产物：CodexQuantBot-Windows-EXE，29.9 MB。此结果替代上面的未构建状态。当前会话尚未下载或启动EXE，未核验EXE本身SHA-256；公开页面的产物摘要属于上传归档而不是内部EXE。API代理域名配置仍待应用。
