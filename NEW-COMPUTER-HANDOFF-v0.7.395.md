# CodexQuantBot v0.7.395 开发交接

源码仓库：t6541/t6541，main；当前目录：/workspace/t6541。使用既有隔离工作区，不创建额外worktree。

修复账户05手工订单历史GET接口被内部白名单拒绝。只扩展账户05只读历史查询；不修改其他账户权限和交易策略。历史部分来源不可用时继续展示已有成交，提示历史不完整。仍保留最近20条展示及按方向/数量/时间FIFO推算的平仓关联。

离线定向验证99项通过，详情见docs/validation-v0.7.395.json。原基线20项额外策略/执行测试失败未修改，历史报告见docs/validation-v0.7.394.json。未连接交易所或启动实盘。

GitHub API现已可正常读取仓库和Actions记录。main源码推送后自动运行Windows构建和测试；成功后发布GitHub Releases v0.7.395，上传原始EXE及SHA256SUMS.txt，不再上传Actions压缩包。实际构建/发布结果需查证，不得把预计下载地址描述成已验证可用。

原电脑凭据、生产数据库、日志不进入仓库或构建包；未经当轮明确授权，不启动桌面或实盘，不下单、撤单、改单、平仓或修改杠杆。

## 构建和发布已完成

Windows运行37481736361成功，构建提交56c48b5。已发布GitHub Releases v0.7.395，原始EXE直接下载地址：https://github.com/t6541/t6541/releases/download/v0.7.395/CodexQuantBot-v0.7.395-Slots-Base-Switch.exe 。当前云端已下载EXE并验证SHA-256一致、PE格式和AMD64架构，未启动程序。

SHA-256：5f58a51a11336c7029351f3dd8d23664b43eacbf5370e4f651cb714265d01c09

GitHub仓库/构建状态API可用；详细构建日志下载跳转到results-receiver.actions.githubusercontent.com，目前该单独域名仍受网络策略限制。Windows测试步骤成功，99项是Linux离线测试的实测计数，不冒充已经读取Windows详细日志。
