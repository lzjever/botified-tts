# Botified TTS 工作约束

- 只允许在当前 `botified-tts` 仓库内创建、修改、删除、格式化、清理、暂存、
  提交或推送文件；其中 `.reference/**` 仅供读取。
- `../botified`、`../botified-asr`、`.reference/**` 以及其他参考或上游仓库均为
  只读，不得 edit、format、clean、stash、commit 或 push。
- 用户对仓库外操作的例外授权必须精确到目标和动作，且仅当次有效；完成后立即
  恢复只读边界。
- 所有适配器、集成代码和 companion 都必须实现于当前仓库。
- 服务和 companion 接收已经适合朗读的纯文本；不增加 Markdown/SSML parser。
- 其余开发原则遵循 `docs/development-constraints.md`。
