# AI 邮件机器人操作手册（按工具能力选择入口）

> 本文件是给 ChatGPT、Codex 和其他自动化助手的操作契约。先识别当前实际可用工具，再选择入口；**不得**假设具备终端、GitHub 写权限、图片二进制上传或定时任务权限。用户只要求查询时禁止发送。未经明确授权，不得创建测试信、循环计划、取消任务或改动 SMTP 配置。

## 入口选择（按优先级）

| 当前工具 | 应选入口 | 可执行功能 | 限制 |
| --- | --- | --- | --- |
| GitHub 连接器提供 `create_file`、`fetch_file` / `fetch` | [普通 ChatGPT 请求文件](../mail-requests/README.md) | 单次自定义文字、随机来信、小龙娘来信、单次延迟或随机目标时间；读取运行和任务状态 | **不能**用此入口创建循环、取消任务、上传图片、变更收件人 |
| 已授权终端、仓库 checkout、Python 3.12 | [命令行](../README.md#助手与命令行) | 查询、单次发送、延迟、重复计划、暂停、取消任务；具备二进制和网络能力时可处理漫画 | 必须明确指定 `--repo`，先读 `AGENTS.md`；不能假设环境有令牌 |
| 已配置自定义 GPT Action | [GPT Action](gpt-actions.md) | 仅 schema 暴露的动作及只读查询 | 尚未完成自定义 GPT 端到端验证；不能继承普通 GitHub 连接器认证 |
| 人工使用 GitHub 网页 | [统一工作流](../README.md#唯一入口) | 选择工作流已声明的 `workflow_dispatch` 选项 | UI 可用选项以当前 `.github/workflows/random.yml` 为准 |
| 独立部署并授权的 HTTP 服务 | [聊天邮件服务](chat-mail.md) | 仅一次性自定义文字 | 未部署不能调用；不可把 Actions 当常驻服务器 |

**工具不具备时停止并说明缺失能力**，不要改用 Gmail、请求明文密钥或偷偷修改代码以绕过限制。

## 普通 ChatGPT：已验证的一次性文字邮件

2026-10-15 已在 `xurenqi/mail-bot` 完成 GitHub `create_file` → `push` → GitHub Actions → QQ SMTP → 收件人确认收到的真实链路测试。此结论仅适用于该仓库、当次权限和文字请求，不等于所有入口均已验证。

1. 确认用户明确要求发送或安排；只读问题只查状态。提醒：**本仓库公开，文件标题、正文及 Git 历史公开**，不得放私人敏感内容、邮箱地址、令牌或 SMTP 授权码。
2. 生成**一次**规范小写 UUID，保持整个请求生命周期不变。仅在已授权仓库 `xurenqi/mail-bot` 的 `main` 分支创建一个**新文件** `mail-requests/<UUID>.json`；不得覆盖、修改、删除旧请求；每次提交最多一个新请求。
3. 内容为 UTF-8 JSON：`{"version":1,"request_id":"<UUID>","action":"发送自定义邮件","subject":"非空单行标题","body":"非空正文","timing":"now"}`。请求编号与文件名完全一致；不要添加未支持的字段。对于 `发送随机来信` 或 `发送小龙娘来信`，省略 `subject`、`body`。可选 `expires_at` 必须带时区。
4. `timing` 支持 `now`、`after:2m`、`random:5m:2d`、带时区 ISO 8601 日期时间。**延迟的是发送，不是未来生成正文**；Actions 排队/轮询会延迟，不保证准点。
5. 提交后保存 UUID 和 commit SHA；通过 GitHub Actions 按 commit SHA 查 `push` 运行，及 `mail-bot-state` 分支的 `.mail-bot/tasks.json` 查询相同 UUID。状态 `queued` 不是已发送；`accepted` 只代表 SMTP 接受，收件人确认收到才算投递验证。
6. 如果创建文件返回超时、错误或结果不确定，**先查同一路径、相同 UUID、任务账本和 Actions**。禁止换 UUID 重发、禁止重跑发送作业。失败/不确定需用户再次明确授权新请求。

该文件入口只接受 `发送自定义邮件`、`发送随机来信`、`发送小龙娘来信`。不能用请求文件写入 `repeat_rule`、`image_path`、`取消任务`、`关闭计划` 等字段；校验器会拒绝。绝不能通过修改工作流或安全检查来“扩展”能力。

## 其他功能与正确操作

- **只读状态**：优先查 `mail-bot-state:.mail-bot/tasks.json` 中指定 UUID 和匹配的 Actions 运行；区分 `staging`、`queued`、`sending`、`accepted`、`failed`、`uncertain`、`expired`、`cancelled`、`blocked`。只读操作不提交请求。
- **循环/暂停/取消**：仅在有适当终端或已配置的受支持工作流操作时使用 `scripts.control_bot` 或工作流已声明动作；参见根目录 README。停止重复计划不能撤回已接受的邮件；不要停用整个工作流。
- **漫画/图片**：普通 `create_file` 不上传图片；用 [漫画说明](comic-mail.md) 中的草稿 Release 手机流程，或已授权的二进制上传工具/终端。不得把图片 base64 塞进公开 JSON，也不得发布临时草稿 Release。
- **自定义 GPT**：按 [GPT Action 配置](gpt-actions.md) 单独认证。普通聊天的 GitHub 连接器权限不会自动传给 GPT。
- **独立聊天 HTTP 服务**：只有服务实际部署、HTTPS 与认证可用才按 [服务文档](chat-mail.md) 调用；否则使用已经验证的文件入口。
- **定时**：若用户现在要求“明天收到”，可在当前授权下创建未来发送任务；若要求“明天才生成新内容/图片”，必须另有具备相应工具和授权的未来执行环境，不可混淆。

## 不破坏现有功能的原则

所有入口复用同一任务账本、SMTP、文案去重和清理机制。不得重置状态分支、已使用文案账本、历史快照、任务 tombstone；不得修改 Secrets、SMTP 收件人、cron、重复计划或安全校验，除非用户单独明确授权且经过检查。文档更新本身不应创建 `mail-requests/*.json`，也不应发送测试邮件。
