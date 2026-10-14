# 小澪聊天邮件入口

外部生成的邮件（包括“紫瞳夜话”）通过 `POST /chat-mail/tasks` 创建 `custom` 任务，
与随机库存完全独立。入口不调用 SMTP，不发送测试信，不创建循环任务。
任务默认目标时间为创建时间，交给现有 `random.yml → task_runner → TaskStore → send_mail → QQ SMTP`
处理；Actions 轮询可能延迟。201 仅表示入队，不代表 SMTP 接收或投递成功。

## 部署

GitHub Actions 没有常驻 HTTP 服务。请在独立运行环境启动此入口，或在现有 Python
服务中调用 `scripts.chat_mail.create_task`。不要把 HTTP 服务放进 Actions。

通过部署环境的秘密管理配置 `CHAT_MAIL_TOKEN`（强随机 token）及 `GH_TOKEN`
（有目标仓库 Contents 读写权限的 GitHub 身份）。两者用途不同，调用者仅获得前者。
入口不需要 QQ SMTP 凭据；QQ 地址和授权码仍仅在现有 Actions Secrets 中。
不能仅在 Actions Secrets 中配置 CHAT_MAIL_TOKEN，独立入口环境也必须有此配置。

```sh
python -m scripts.chat_mail --repo xurenqi/mail-bot --port 8080
```

服务只监听 `127.0.0.1`；对外通过 HTTPS 反向代理转发，配置请求大小、超时和限流。
此标准库服务是简单的串行创建适配器。需要高并发时在现有生产服务中调用 create_task。
保持目标仓库原有 Actions 启用、SMTP Secrets 和轮询设置，默认约每小时检查一次，
现有 `MAIL_BOT_POLL_MINUTES=5` 配置可使用五分钟检查。入口不会修改这些设置或触发 dispatch。

## 调用

先写入 UTF-8 文件 `chat-mail.json`：

```json
{
  "subject": "紫瞳夜话",
  "body": "这里是外部生成的完整正文。",
  "request_id": "7d410fd0-596c-4f82-99f9-e9b636ff98cd"
}
```

```sh
curl --request POST https://your-mail-entry.example/chat-mail/tasks \
  --header "Authorization: Bearer ${CHAT_MAIL_TOKEN}" \
  --header 'Content-Type: application/json' \
  --data-binary @chat-mail.json
```

request_id 必须为规范小写 UUID。同一请求始终使用同一编号，不要把 token 写入 JSON。
仅接受上述三个字段，repeat、repeat_rule、timing 等额外字段均拒绝。
主题非空单行、最多 200 字符；正文非空、无空字符、最多 100000 字符；请求最多 512 KiB。

成功创建返回 HTTP 201：

```json
{"task_id":"7d410fd0-596c-4f82-99f9-e9b636ff98cd","status":"queued","created":true,"duplicate":null,"duplicate_of":null,"repeat":false}
```

重复请求返回 200、created=false；同 request_id 不覆盖原内容、不重新上传。
同一 UTF-8 正文 SHA-256 即使更换标题或 request_id，也不会创建可发送的副本。
新编号会留下 cancelled 去重记录，duplicate_of 指向原任务；终态记录不删除。
账本仅保存 hash 和私有草稿 Release asset 引用，不存正文或 token。
subject/body 快照的 SHA-256 与 private_payload 及原有 worker 去重完全兼容；
对已有非聊天 custom 任务，可匹配其完整 subject/body 快照 hash。
正文 hash 保守预留也适用于 staging、failed、uncertain、cancelled，避免失败或不确定后自动重发。

无 token、错误 token 或服务未配置 token 返回 401；无效参数返回 400。
GitHub 写入或上传未确认返回 503，可能已留下 staging/failed 记录。
此时先通过现有状态工具查询原 request_id，禁止更换编号或自动重试上传、发送。

```sh
python -m scripts.control_bot --repo xurenqi/mail-bot status
```

入口通过 TaskStore 的 SHA CAS 原子预留 request_id 和 hash，再调用 private_payload 保存正文，
最后转为 queued。并发 CAS 冲突仅重试账本纯转换，不重试上传或 SMTP。
账本 repeat=null 表示现有系统的一次性任务，接口对应返回 repeat=false。
发送结果、发送租约、快照清理和 SMTP 防重复仍由现有任务系统处理。
