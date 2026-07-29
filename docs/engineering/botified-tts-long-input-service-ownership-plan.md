# Botified TTS 长文本服务端处理改进计划

> 状态：已收敛，可交付开发
>
> 目标版本：`v0.2.2`
>
> 适用范围：当前 `botified-tts` 仓库的一次原地产品改进
>
> 基线：根目录 `AGENTS.md`、`docs/development-constraints.md`

本文是本轮独立施工计划，不是项目的全局真相。若本文与项目约束冲突，以
`AGENTS.md` 和 `docs/development-constraints.md` 为准。

## 1. 问题

当前 `POST /v1/speech` 接受最多 8192 UTF-8 bytes。`tts` Skill 因此要求 Agent
在超限后征求用户同意，再由调用方切分、编号、分别合成和发布多个文件。

这带来三个直接问题：

1. 文本切分存在服务端 `Segmenter` 和 Agent 手工切分两种做法，职责重复；
2. Agent 容易错误估算 UTF-8 大小，并可能生成多个听感不连续的文件；
3. HTTP handler 会把全部 PCM chunk 收集到内存，随后再次聚合为 WAV 或 Ogg；
   直接提高输入上限会同步放大内存占用。

本次实际调用进一步确认了问题边界：

- Agent 一次提交了约 2415 UTF-8 bytes，而不是超过 8192 bytes；
- 服务在同一个请求中内部切成 27 段；
- 最终得到 172.32 秒音频；
- 因此这次音色、音量和表达变化不是 HTTP 上限或调用方拆分造成的。

本轮只收敛长文本的调用职责和 HTTP 文件生成资源路径，不把它描述成长语音音质
修复。

## 2. 产品决定

面向一个期望输出文件的文本，唯一工作流为：

```text
一份完整、适合朗读的纯文本
  → 一次 speak
  → 一次 POST /v1/speech
  → 服务端 Segmenter 内部切分
  → 同一个 SpeechService 逐段生成
  → 一个完整 WAV 或 Ogg
  → 一次 publish_file
```

HTTP `text` 上限固定提高到 **16 KiB UTF-8**。

选择 16 KiB，而不是 32 KiB 或 64 KiB，原因是：

- 已经是当前容量的两倍，足以覆盖远超普通 Agent 回复长度的文本；
- 按本次 2415 bytes 生成 172.32 秒音频的结果粗略外推，16 KiB 可能对应约
  20 分钟音频，已经是同步完整文件接口的合理上界；
- 同样外推时，64 KiB 可能对应约 78 分钟音频和约 10 分钟推理，接近或超过
  bundled helper 当前 600 秒等待时间，也会长期占用 GPU、连接和临时磁盘；
- HTTP 完整文件与可边生成边消费、可取消的 WebSocket session 具有不同资源
  语义，不为数字对称而把 HTTP 上限提高到 64 KiB。

上述音频时长和推理时间只用于容量取舍，不是产品时长承诺；实际结果会随语言、
标点、标签、voice mode 和硬件变化。

上限是服务内部固定常量，不增加环境变量或请求字段。若以后真实 Botified 回复
经常触顶，再根据实际需求原地调整同一个 owner。

## 3. 范围

### 3.1 本轮完成

- 将 HTTP `text` 上限从 8 KiB 提高到 16 KiB UTF-8；
- 保持 HTTP 完整文本由现有 `Segmenter` 和 `SpeechService` 内部处理；
- 将 HTTP 音频生成从全量内存聚合改为请求级临时文件；
- 保持一个请求只返回一个完整 WAV 或 Ogg；
- 更新 `tts` Skill，删除 Agent 自动拆分、编号和多文件 fallback；
- 保持 helper 一次 `speak` 只发送一次 HTTP 请求；
- 更新 README、现有 owner 测试和版本号；
- 完成当前仓库的固定版本 Docker 镜像发布。

### 3.2 明确不做

- 不修改 VoxCPM2、Nano-vLLM-VoxCPM、推理参数或模型 revision；
- 不修改 `Segmenter` 的 natural/short 阈值、边界规则或 deadline；
- 不修改 `SpeechService` 的请求级固定锚点、voice、mode 或 style 语义；
- 不把 controllable 自动切换为 faithful，不生成或猜测参考音频 transcript；
- 不增加响度归一化、crossfade、重试、随机种子、音色纠偏或其他音频后处理；
- 不修改 WebSocket append、session、PCM 输出、取消或超时语义；
- 不增加 long-form endpoint、HTTP 增量上传、异步 Job、进度查询、下载服务或
  cancel endpoint；
- 不增加输入上限、临时目录或编码器的环境变量和请求级选项；
- 不增加缓存、对象存储、后台临时文件清理器或恢复机制；
- 不修改 helper 的依赖边界，不把 `publish_file` 实现进 helper；
- 不修改 companion、Botified Core、`.reference/**` 或任何仓库外项目；
- 不增加 Markdown/SSML parser、质量评分、测试矩阵或开发治理产物；
- 不批量回写已经完成的历史施工计划。

## 4. 固定行为

### 4.1 输入和错误

- `POST /v1/speech` 的 `text` 恰好 16384 UTF-8 bytes 时允许进入合成；
- 超过 16384 UTF-8 bytes 时在推理前返回现有 HTTP 413 和
  `input_too_large`；
- 空文本、未知字段、认证、voice、mode、style 和 `Accept` 的现有语义不变；
- WebSocket 单 append 16 KiB、单 session 64 KiB 的现有上限不变；
- 错误消息从唯一 HTTP 上限常量得到数字，不在 schema 和文档实现中维护第二份
  运行时常量。

### 4.2 服务端分段

- HTTP handler 把完整 `text` 只交给一个现有 `Segmenter`；
- 分段结果只进入一次现有 `SpeechService.synthesize()`；
- 调用方不预切句，不根据 voice mode 选择另一套分段方式；
- 所有片段继续复用同一个请求的 voice、mode、style 和固定声音锚点；
- 本轮不改变单段模型输入长度，因此不以音色或风格稳定性改善作为验收结果。

### 4.3 HTTP 文件生成

HTTP 在完整音频准备好以后才返回成功响应，保留当前“非流式完整文件”语义和在
发送响应前返回稳定错误码的能力。

每个请求只使用一份专属系统临时目录：

1. `SpeechService` 输出的 PCM chunk 到达后立即顺序写入临时 PCM 文件；
2. 不建立 PCM chunk list，不执行 `b"".join(PCM)`；
3. WAV formatter 分块读取 PCM，写出固定 mono、48 kHz、s16le WAV；
4. Ogg formatter 让 FFmpeg 从 PCM 文件读取并直接写 Ogg/Opus 目标文件，不通过
   Python stdin/stdout 保存完整音频；
5. 最终文件通过 `FileResponse` 返回，保留精确 MIME、`Content-Length` 和
   `Vary: Accept`。

WAV 和 Ogg 共用：

```text
SpeechService PCM → 请求级 PCM 文件 → 唯一格式 formatter → 完整响应文件
```

不保留原来的内存聚合 fallback。

### 4.4 临时资源 owner

- 返回 response 前，HTTP handler 拥有临时目录和其中全部文件；
- 生成、编码或取消失败时，handler 在释放 admission 前清理临时目录；
- 成功创建 response 后，临时目录 ownership 一次性交给私有 `FileResponse`
  wrapper；
- wrapper 在 `__call__` 的 `finally` 中清理，覆盖正常发送、发送异常和 client
  disconnect；
- 不只依赖 Starlette background callback，因为发送被取消时不能保证 callback
  执行；
- Ogg 编码继续在线程池中执行现有有界 `subprocess.run(..., timeout=30)`，只把
  输入和输出改成文件，不增加 async 子进程或取消状态机；
- 不主动轮询 client disconnect；响应发送前断连时，本次生成可能继续完成；
- 外层任务取消不会同步停止已经进入线程池的 FFmpeg，工作线程最迟在正常完成或
  现有 30 秒 timeout 后结束，不增加额外回收机制；
- 进程崩溃依赖容器系统临时目录生命周期，不增加 registry、sweeper 或启动恢复。

现有 admission 仍只覆盖生成和编码；完整文件交给 response 后即可释放 GPU
capacity，并完成当前 terminal summary。传输阶段发生断连时只清理 response
拥有的文件，不把已经完成的 summary 从 `ok` 改写为 `cancelled`。每个请求仍只
记录一条 summary，不增加日志 schema。

### 4.5 Skill 和 helper

`skills/tts/SKILL.md` 将长度工作流改为：

- “one complete speakable response intended to become one audio file”；
- 一份文本只调用一次 `speak`，服务负责自然分段；
- Agent 不估算 UTF-8 bytes、不截断、不预切句、不自动重试；
- 服务返回 `input_too_large` 时，报告限制并请用户缩短内容；
- 不再提供“确认后拆分、编号、生成多个独立文件”的第二条路径；
- Agent 在 helper 成功后只调用一次 Botified `publish_file`；
- `publish_file` 失败或结果不明时报告失败并停止，不重试，不读取 channel 配置、
  credential 或 access token，不构造 channel HTTP 请求；
- 不把 service/channel token 放入 Bash 命令或输出，不重新编码，不直接调用
  Matrix 或其他 channel API，不重复发布。

现有 helper 已经把一个 `--text` 原样放入一个 JSON，并只发送一次
`POST /v1/speech`；它也会分块下载响应到同目录临时文件后原子落成。因此 helper
不增加长度判断、拆分器、新 flag、第三方依赖或发布逻辑，600 秒等待时间保持不变。

## 5. 实现位置

| 文件 | 改动 |
|---|---|
| `src/botified_tts/schemas.py` | HTTP 上限改为 `16 * 1024`；错误数字由该常量生成 |
| `src/botified_tts/app.py` | 删除 PCM list 和 `Response(content=...)`；逐块落盘；实现 handler/response 资源 ownership、清理和现有错误映射 |
| `src/botified_tts/audio.py` | 增加文件 formatter；保留非 HTTP 使用的薄 WAV bytes helper并复用同一 WAV writer；删除 Ogg bytes formatter；保留现有有界 FFmpeg timeout |
| `tests/test_schemas.py` | 就地更新 UTF-8 exact/+1 边界 |
| `tests/test_api.py` | 更新现有 413 case；覆盖旧上限以上文本的一次请求、内部多段和单文件响应；覆盖临时资源清理 |
| `tests/test_audio.py` | 就地调整文件 formatter 的固定 WAV/Ogg 和编码失败测试；保留薄 WAV bytes helper 的现有格式行为 |
| `skills/tts/SKILL.md` | 删除客户端拆分流程；明确一份文本、一次调用、一个文件、一次发布 |
| `README.md` | 更新 HTTP 上限和完整文本由服务内部处理的当前事实 |
| `pyproject.toml`、`uv.lock` | 版本更新到 `0.2.2`，不改变依赖 |

`speech.py`、`segmenter.py`、`streaming.py`、VoiceStore、helper 脚本、companion、
Dockerfile 和模型 spec 不改。

## 6. 最小测试

只测试本项目直接拥有的新行为：

1. 复用 `tests/test_schemas.py::test_http_text_limit_counts_utf8_bytes`，证明
   16 KiB 精确接受、增加一个 UTF-8 byte 后拒绝；
2. 更新 `tests/test_api.py` 现有 oversize error case，只验证 HTTP 413 和
   `input_too_large` 映射，不重复 schema 精确边界；
3. 在现有 HTTP speech 行为附近使用一个大于旧 8 KiB、小于等于 16 KiB 的输入，
   证明只有一个 HTTP 请求和一次 `SpeechService` 调用，内部得到多个 segment，
   拼接后文本逐字符不变，最终只有一个完整音频响应；
4. 原地调整现有 WAV/Ogg audio tests，证明文件 formatter 保持 mono、48 kHz、
   s16le WAV 和可完整解码的 Ogg/Opus；
5. 在现有 Ogg encoder failure API 用例中证明 handler-owned 临时目录被清理、
   admission 可再次使用；
6. 用一个 response-level 用例让发送抛出 `CancelledError`，只证明 ownership
   交接后 wrapper 的 `finally` 清理临时目录；
7. 保留现有 helper 单请求、分块下载、MIME 校验、原子输出和标准库测试，不新增
   “helper 没有 splitter”或 Skill 文案测试；
8. `publish_file` 属于 Agent 与 Botified 的现有行为，不在本仓库模拟或重复测试；
9. 本轮不改变模型、CUDA、分段算法或声音锚定，不新增 GPU 音质或长文本测试。

完成代码后运行现有普通测试：

```bash
uv run pytest -q
```

不增加新的测试入口、测试脚本、报告或检查层。

## 7. 施工顺序

### 阶段一：固定输入边界

- 把 HTTP 上限改为 16 KiB；
- 从唯一常量生成错误数字；
- 就地更新 schema 和 API 413 测试。

阶段结果：服务在推理前准确接受 16 KiB、拒绝超限文本，其他协议不变。

### 阶段二：原地替换 HTTP 音频聚合

- 把 HTTP 生成改为逐 chunk 写请求级临时 PCM；
- 原地替换 WAV/Ogg 文件 formatter；
- 返回带确定资源 owner 的 `FileResponse`；
- 完成 handler 失败/取消清理和 response 发送取消清理，保留现有有界 FFmpeg
  timeout；
- 删除 HTTP bytes 聚合路径、Ogg bytes formatter 和 fallback；保留真实 GPU
  integration 使用的薄 WAV bytes helper，并与文件 formatter 共用 WAV writer。

阶段结果：扩大后的请求不会在 Python 内存中同时保存多份完整音频。

### 阶段三：Skill、当前文档和发布

- 修改 Skill 的长度与发布工作流；
- 更新 README 的当前上限和服务端分段说明；
- 更新版本到 `0.2.2`；
- 运行现有普通测试；
- 从同一个 clean commit 创建固定 `v0.2.2` tag；
- 按现有唯一发布方式构建并发布
  `ghcr.io/lzjever/botified-tts:v0.2.2`；
- GitHub Release 只指向固定镜像，不增加 `latest`、额外 registry、release
  asset、workflow 或发布脚本。

阶段结果：Docker 用户、repo 开发者和 Botified Skill 使用同一套行为。

## 8. 验收标准

- HTTP `text` 恰好 16384 UTF-8 bytes 可进入合成，增加一个 byte 后返回 HTTP
  413 `input_too_large`；
- 大于旧 8 KiB 的一份文本只产生一次 HTTP 请求和一次 `SpeechService` 调用；
- 服务通过现有 `Segmenter` 形成多个内部片段，文本内容和顺序完全保留；
- 所有片段使用同一请求的 voice、mode、style 和固定声音锚点；
- 最终只返回一个 MIME 正确、容器结构完整且可完整解码的 WAV 或 Ogg；
- HTTP 完整音频不再以 PCM chunk list、连续 PCM 和最终响应 bytes 的多份副本
  同时保存在 Python 内存中；
- handler 在成功交接、生成错误、编码错误或任务取消时清理自己拥有的临时路径；
  response 在正常发送或发送取消后清理已经交接的响应文件；
- admission 在 handler 的所有 terminal path 释放；文件交接后发生传输断连只
  清理 response 文件，不改写已经完成的 `ok` summary；每个请求只记录一条现有
  summary；
- Skill 只指导“一份可朗读文本 → 一次 speak → 一个文件 → 一次
  publish_file”，不再估算字节、拆分、编号、直接调用 channel API 或重复发布；
- helper 仍只依赖 Python 3.10+ 标准库，CLI、JSON、MIME、原子输出和 600 秒等待
  语义不变；
- HTTP/WS endpoint、JSON schema、Accept、错误码、Voice Profile、Voice Design、
  controllable、faithful、style 和非语言标签能力保持兼容；
- WebSocket、分段阈值、声音锚定、模型、companion 和仓库外项目不变；
- 普通测试通过，不新增重复测试、GPU 音质矩阵或测试治理层；
- 根包、Git tag、GHCR 固定镜像和 GitHub Release 均为 `v0.2.2`；
- 只修改当前仓库；`.reference/**` 和所有仓库外项目保持只读；
- 不宣称本轮解决 172 秒音频中的随机音色、音量或表达变化。
