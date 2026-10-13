# ChatGPT 图片邮件：Base64 暂存入口（设计与安全门槛）

> 状态：**设计稿，不是可用的发送入口**。本分支不会上传图片、触发 Actions 或 SMTP。不得将此文件当作已部署的功能。

## 目标

ChatGPT 已能读取生成的 PNG 并调用 GitHub `create_file`（仅 UTF-8 文本）；当前连接器没有 GitHub Release 二进制上传工具。希望在不引入付费图片 API、Gmail 或新服务器的情况下复用 QQ SMTP、`.mail-bot/tasks.json` 与现有最多一次 SMTP 尝试机制。

## 必须先解决的阻断条件

1. **私有暂存**：主仓库 `xurenqi/mail-bot` 是公开仓库。禁止将 PNG 的 Base64、正文中的隐私信息或附件分块提交到其任何分支；删除 Git 文件不会清除历史。推荐独立**私有**暂存仓库，且先核验 ChatGPT GitHub 工具确实有该仓库 Contents 写权限，以及 Actions 可安全读取它。不要假设主仓库的 `GITHUB_TOKEN` 能读取另一私有仓库。
2. **连接器负载上限**：约 2.2 MiB PNG 的 Base64 约 3 MiB，必须先验证 `create_file` 的单次输入/请求限制。仅允许有界分块，最多一次创建每块；返回不确定只能查询同一路径，禁止换 UUID/重写/重复提交。
3. **触发路径**：GitHub Actions 的 `push` 触发发生在被推送的仓库。写入私有暂存仓库不会自动触发主仓库工作流。需要经认证、所有者限定的显式调度或由私有仓库工作流安全地发起 `repository_dispatch`；当前聊天未验证有相应工具。不得用公开 PR、Issue、任意 URL 或未验证的 `workflow_dispatch` 代替。
4. **现有任务内容准备依赖草稿 Release**：`scripts/task_runner.py` 的 `private_payload` 和 `private_image` 仍调用 `upload_release_asset`。新入口即使恢复出图片，也不能绕开该依赖直接宣称可发送。要么让 GitHub Actions 运行器完成受控 Release 上传，要么重构为安全的私有暂存引用并保留校验/清理。
5. **隐私**：图像像素和 Base64 等价，不能把编码当加密；避免将私有仓库附件、下载 URL、token 或正文写入日志、公开状态分支或 Git 提交消息。

## 拟议数据协议（只在私有仓库）

路径：`incoming/<canonical-lowercase-uuid>/manifest.json` 和 `incoming/<uuid>/parts/0000.txt` 等。

manifest 包含：`version=1`、规范 UUID、`image_sha256`、`image_bytes`（<=5 MiB）、`mime`、`part_count`、每块 SHA-256、标题正文的 SHA-256、状态 `staging`。实际邮件正文另放私有暂存并校验。每块只含 Base64 字符，解码后逐块校验，合并校验图片整体 SHA-256，并复用 `scripts.comic_mail.image_type` 的结构/尺寸校验。

在全部分块可读且通过校验前**不允许触发 SMTP**。最终发送前必须将同一 UUID 写入现有 `mail-bot-state` 任务账本，采用现有 CAS 原子占用；发送中/已接受/不确定均禁止重发。不得因清理失败重跑 SMTP。

## 分阶段验收

- [ ] 确认独立私有暂存仓库和最小权限（不要求用户在聊天中粘贴密钥）
- [ ] 只用小尺寸无隐私测试图片验证单块/多块上传；测试不触发邮件
- [ ] 编写离线单元测试：格式、大小、哈希、缺块、重复块、越权、并发、响应丢失
- [ ] 验证 Actions 能读取私有暂存且不泄露内容
- [ ] 复用统一任务账本和最多一次 SMTP 尝试逻辑，确认结果查询与附件清理
- [ ] 经用户再次明确授权后，才提交一次真实图片邮件

## 现有可用替代路径

仓库 `docs/comic-mail.md` 已提供**手动上传到草稿 Release** 的完整流程，支持 `draft:文件名` 登记并发送；此流程无需 Base64 改造。自动上传的真正阻断点是当前聊天缺少二进制 Release 上传工具，不是邮件机器人不会发送图片。
