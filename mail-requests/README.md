# 加密的一次性邮件请求入口

本仓库是公开仓库。请求文件仍会进入 Git 提交历史，但版本 2 文件只包含随机请求编号和加密信封，不包含主题或正文。版本 1 明文请求已禁用。

## 首次配置

需要 OpenSSL、Python 3.12 和仓库本地副本。执行以下步骤前，先将加密代码合并到 main。

1. 在仓库根目录生成密钥。私钥必须放在仓库目录之外：

   python -m scripts.request_crypto keygen --private-out ..\mail-bot-request-private.pem --public-out mail-requests\public_key.pem

2. 在 GitHub 仓库 Settings → Secrets and variables → Actions 添加 repository secret：

   - 名称：MAIL_REQUEST_PRIVATE_KEY
   - 值：mail-bot-request-private.pem 的完整 PEM 内容

   私钥不能提交到 Git、写入邮件、普通工作流输入或聊天。妥善保存私钥副本；GitHub 不会显示已保存的 Secret。

3. 只提交 mail-requests/public_key.pem 到 main。公钥可以公开。不要把私钥文件加入提交。

缺少 Secret 或密文无法解密时，任务会停止，不创建任务，也不调用 SMTP。普通代码推送不会发送邮件。SMTP 仍需要已有的 QQ_EMAIL、QQ_SMTP_AUTH_CODE 和 MAIL_TO Secrets。

## 创建请求

发送端必须先使用公钥加密请求，再通过有 main 写入权限的 GitHub 工具新增 mail-requests/<小写 UUID>.json。可在本地仓库运行 python -m scripts.request_crypto encrypt --public-key mail-requests\public_key.pem，从标准输入读取明文 JSON，并从标准输出写出密文 JSON。不要把明文 JSON 保存到仓库或提交历史。

明文 JSON 的字段与值：

- 必填：version: 1、request_id、action
- action 为“发送自定义邮件”时还要提供非空 subject 和 body
- 可选：timing、expires_at
- 日常来信可用“发送随机来信”或“发送小龙娘来信”，不提供主题和正文

加密后文件是版本 2 信封，包含 version、request_id、encrypted。每次推送只新增一个 UUID 请求文件；只允许仓库所有者直接推送到 main。修改、删除或重命名文件不会再次发送，也不会取消已创建的任务。

只有具备本地命令执行能力的 ChatGPT/Codex 才能在写入前运行加密工具。只有 GitHub create_file 工具、没有本地加密能力的普通 GPT 不能安全提交自定义正文。

## 数据经过的位置

RSA-OAEP-SHA256 用于封装随机密钥，AES-256-CBC 与 HMAC-SHA256 用于加密并验证请求内容。Actions 使用 MAIL_REQUEST_PRIVATE_KEY 解密后，仍调用原有 task_runner，写入同一任务账本并使用原有防重复与 QQ SMTP 流程。正文随后存入私有草稿 Release；任务结束时附件清理逻辑会删除正文附件，账本保留任务状态和内容哈希。

密文在公开仓库和 Git 历史中可见；私钥只有仓库 Actions Secret 和受信任的 Actions 运行环境使用。任何能管理仓库 Secrets、工作流或 Actions 的仓库管理员都属于受信任方。

## 不要重试不确定的请求

创建文件后，只查询对应任务和 Actions 运行。SMTP accepted 表示 SMTP 接受了提交，不保证最终到达。若结果不确定，不要换 UUID 或重建请求；先核验收件箱与任务历史。

原先提交过的明文请求不会因加密改动而从 Git 历史消失。版本 2 加密入口的首次完整发送验证，需要配置密钥后由仓库所有者发起；代码部署本身不发送邮件。
