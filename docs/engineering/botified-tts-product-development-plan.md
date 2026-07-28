# Botified TTS 产品开发计划

> 状态：产品与架构收敛完成，可交付开发
> 目标仓库：`botified-tts`
> 研究基线：2026-07-27
> 项目约束：`docs/development-constraints.md`；发生冲突时以项目约束为准
> 上游审阅基线：VoxCPM `616d3d3e630a9c96c2853250eef91b0f39dcd5fa`、
> Nano-vLLM-VoxCPM `0ef61b0ba634dbf2fad9e916bc4fb696a3c0f51f`

## 1. 产品定义

Botified TTS 是一个面向 Botified 的独立、轻量、CUDA-only TTS 服务。

它只解决一件事：

> 把 VoxCPM2 的主要语音合成能力封装成稳定、方便的 HTTP 非流式接口和
> WebSocket 双向流式接口。

产品不是通用语音平台，也不负责语音内容生产之后的渠道发布、播放进度、
多租户治理或长期语音资产管理。

首版必须做到：

1. 使用 VoxCPM2 和 Nano-vLLM-VoxCPM。
2. 支持普通合成、Voice Design、可控音色克隆和高保真音色克隆。
3. 支持自然语言风格控制和 VoxCPM2 原生非语言标签。
4. 提供一个 HTTP 非流式合成入口，直接返回 WAV。
5. 提供一个 WebSocket 双向流式入口，接收任意粒度的增量文本并输出 PCM。
6. 服务端完成句子级分段，并利用上一完整语音段保持跨段连续性。
7. 支持可复用音色的创建、列表和删除。
8. 无 CUDA 时在模型下载和加载前明确失败，不尝试 CPU。
9. 使用 Docker Compose 一键部署。
10. 提供一个最小 Agent Skill。

## 2. 设计原则

### 2.1 KISS

- 一个服务部署单元。
- 一个 VoxCPM2 模型。
- 一个服务实例使用一张 GPU。
- 一个合成核心同时服务 HTTP 和 WebSocket。
- 一个本地目录保存注册音色，不引入数据库。
- 一个 Docker Compose 部署方式。
- 仅支持当前 Botified 所需的纯文本输入和两种输出方式。

### 2.2 DRY

- HTTP 与 WebSocket 共享同一个 `SpeechService` 和 Nano adapter。
- 长文本与增量文本共享同一个分段器。
- Voice Design、音色克隆和普通合成都映射到同一个 VoxCPM2 generate 调用。
- REST、WebSocket、CLI 和 Skill 使用同一份请求字段定义。
- 音频转换、chunk 输出和错误映射各只有一个实现。

### 2.3 YAGNI

首版不为可能出现的公网、多租户、多模型、多 GPU 或其他 TTS 引擎提前抽象。
只有真实业务需求出现后，才增加兼容层、codec、调度器或持久任务。

### 2.4 一个功能一种做法

| 功能 | 唯一做法 |
|---|---|
| 非流式合成 | `POST /v1/speech` |
| 双向流式合成 | `WS /v1/speech/stream` |
| 注册音色 | `POST /v1/voices` |
| Voice Design | `/v1/speech` 中使用 `voice.type=design` |
| 风格和情绪 | 一个自然语言 `style` 字段 |
| 语气词和非语言声音 | 直接写入 `text` 的 VoxCPM2 原生标签 |
| 跨段连续性 | 上一个完整生成段的 text + generated latents |
| 中断 | 取消并结束当前 WebSocket session |
| 部署 | `./scripts/deploy.sh` |

## 3. 产品边界

### 3.1 首版包含

- Linux x86_64、NVIDIA CUDA。
- VoxCPM2 单模型。
- Nano-vLLM-VoxCPM 单推理后端。
- 单实例单 GPU；不同请求的 batching 交给 Nano。
- HTTP 完整文本输入、WAV 输出。
- WebSocket 增量文本输入、48 kHz mono PCM s16le 输出。
- 普通无参考合成。
- Voice Design。
- reference-only 可控音色克隆。
- reference + exact transcript 高保真音色克隆。
- 自然语言 voice/style instruction。
- 官方推荐的非语言标签。
- 最小本地音色创建、列表和删除。
- 文本自动分段、强制 flush、finish 和 cancel。
- CUDA preflight、模型 warmup、健康检查。
- Docker Compose 一键部署。
- Botified 调用示例和最小 Agent Skill。

### 3.2 首版明确不包含

- OpenAI Audio API 兼容层。
- vLLM-Omni 或可插拔推理后端框架。
- Voice Design candidate、试听列表或 materialize 工作流。
- generation job、artifact、对象存储和异步任务。
- Markdown、SSML、speech parts 或增量 Markdown parser。
- 结构化 emotion、pace、pitch、energy、preset 或 style compiler。
- 对非语言标签再设计一套结构化词汇。
- MP3、Ogg、Opus 或渠道专用编码。
- playback ack、播放水位、回滚和断线恢复。
- session 内切换 voice、style 或 mode。
- WebRTC、电话网关和通话编排。
- 独立 Botified TTS Bridge 产品。
- 多 GPU 调度、sticky routing 和故障迁移。
- LoRA 训练、在线加载或音色微调。
- Prometheus 平台、自动音质评分或大规模 benchmark 矩阵。
- Voice Profile 授权证明、来源核验、滥用检测和合规审计。
- 多租户、用户、组织、RBAC、计费和管理后台。

如果未来出现明确的 OpenAI 客户端兼容需求，可以在 canonical
`SpeechService` 之上增加薄 adapter；该可能性不进入首版实现。

## 4. 上游能力与采用方式

### 4.1 采用依据

VoxCPM2 官方资料确认：

- 支持 30 种语言和多种中文方言；
- 输出 48 kHz 音频；
- 支持 Voice Design；
- 支持无需 transcript 的 reference-only 可控克隆；
- 支持 reference + exact transcript 的高保真 continuation 克隆；
- 支持自然语言控制音色、情绪、节奏和表达方式；
- 支持流式音频输出；
- 长文本推荐按句子拆分生成；
- 原生模型不支持“同一个推理请求内文本不断增长”的双向流式。

参考：

- [VoxCPM README](https://github.com/OpenBMB/VoxCPM)
- [VoxCPM2 Usage Guide](https://voxcpm.readthedocs.io/en/latest/usage_guide.html)
- [VoxCPM2 Voice Cookbook](https://voxcpm.readthedocs.io/en/latest/cookbook.html)
- [Nano-vLLM-VoxCPM](https://github.com/a710128/nanovllm-voxcpm)

因此，本项目的“双向流式”定义为：

```text
客户端持续 append text
        |
服务端形成不可变 sentence/segment
        |
每个 segment 调用一次 Nano streaming generation
        |
服务端持续返回上一 segment 的 audio chunks
```

它是服务层的全双工协议，不宣称 VoxCPM2 支持对同一个未完成推理请求追加
token。

### 4.2 VoxCPM2 模式映射

| API 输入 | VoxCPM2 条件 | 语义 |
|---|---|---|
| 无 `voice`、无 `style` | target text | 普通合成，音色不保证跨请求一致 |
| 无 `voice`、有 `style` | `(style) + text` | 无参考风格合成，音色不保证跨请求一致 |
| `voice.type=design` | `(description + style) + text` | Voice Design |
| profile + `controllable` | right-padded reference latent + optional style | 音色稳定、表达可控 |
| profile + `faithful` | left-padded prompt latent + exact transcript，并附 reference latent | 最大程度复现原声音色和表达 |

规则：

- `faithful` 要求注册音色时提供与 reference audio 精确一致的 transcript。
- 官方说明高保真模式会忽略 control instruction，因此 `faithful + style`
  直接返回 `invalid_request`，不静默忽略。
- `style` 是自然语言字符串。服务只负责使用固定模板添加为模型 control
  instruction，不虚构模型不存在的独立控制头。
- Voice Design 的 `description` 和 `style` 同时存在时，按固定顺序合成一个
  instruction：先 voice description，后 delivery style。
- Voice Design 具有模型原生随机性。需要复用某次结果时，将生成的 WAV 注册为
  profile；首版不暴露 seed。

### 4.3 原生文本能力

服务接收“应被朗读的纯文本”，不解析 Markdown。

客户端可以直接使用官方非语言标签：

```text
[laughing] [sigh] [Uhm] [Shh]
[Question-ah] [Question-ei] [Question-en] [Question-oh]
[Surprise-wa] [Surprise-yo] [Dissatisfaction-hnn]
```

服务不把这些标签映射成另一套 event schema，也不自动插入语气词。

Botified 调用方负责把 Agent 的 Markdown 或结构化回答投影成最终朗读文本。
TTS 服务只负责分段，不负责决定哪些业务内容应该被朗读。

## 5. 最小架构

```text
HTTP / WebSocket
        |
        v
SpeechService
  |        |          |
  |        |          +-- Segmenter / StreamingSession
  |        +------------- VoiceStore
  +---------------------- VoxCPMEngine
                              |
                              v
                    Nano-vLLM-VoxCPM / CUDA
```

### 5.1 `SpeechService`

`SpeechService` 是唯一合成 owner：

```python
async def synthesize(
    options: SynthesisOptions,
    segments: AsyncIterator[str],
) -> AsyncIterator[PCMChunk]
```

- 解析 voice 和 mode；
- 从 `VoiceStore` 读取 reference；
- 构造 VoxCPM2 conditioning；
- 串行生成同一请求/session 的 segments；
- 保存上一个完整段的 continuation；
- 统一输出 PCM chunks。

HTTP adapter 收集该 iterator 并封装 WAV。WebSocket adapter 直接发送同一个
iterator。不得为 HTTP 再实现一套非流式模型调用。

Nano 的内部流由 waveform chunks 和一个正常结束时的 terminal completion
组成；terminal completion 携带当前 segment 的完整 generated latents。
`SpeechService` 消费 completion 并提交 continuation，对外仍只产生 PCM。
cancel、engine error 或客户端发送失败时不得提交未完整生成的 continuation。

`SpeechService` 在 `finally` 中显式关闭当前 Nano async generator。正常耗尽时
关闭是幂等清理；提前退出时 `aclose()` 必须触发 Nano cancel，不能依赖 Python
最终回收 async generator。

### 5.2 `VoxCPMEngine`

- 只封装 Nano-vLLM-VoxCPM，不设计通用 engine interface。
- Nano worker 与 Web 服务属于同一部署单元，不另起一个内部 HTTP 服务。
- 同一 session 的 segment 串行提交。
- 不同 session 的并发与 batching 使用 Nano 原生能力。
- 服务层 request/session ID 不进入 Nano 公共模型协议。
- 服务使用一个进程内 admission counter，最多接纳 16 个 HTTP 合成请求或
  WebSocket session；Nano `max_num_seqs=16` 只控制执行 batch，不承担入口限流。
- `VoxCPMEngine.wait_for_fatal()` 只委托 Nano AsyncPool 的同名公开接口，不轮询
  health，不读取 Nano 私有字段。

顶层 runtime supervisor 同时监督 HTTP serve task 和
`VoxCPMEngine.wait_for_fatal()`。Nano fatal 时先撤销 ready，再请求 HTTP server
有界停服，最后重新抛出 `RuntimeError` 使进程非零退出。正常 shutdown 取消
fatal waiter 后关闭 engine 并正常返回。不能只创建一个无人 await 的 background
task，因为 task exception 本身不会可靠终止服务进程。

### 5.3 `VoiceStore`

- 本地目录是唯一持久化方式。
- 保存标准化 reference WAV、可选 exact transcript 和简单 metadata。
- reference/prompt latents 只放进程内 cache，不持久化。
- 重启后按需重新编码，避免 latent schema、fingerprint 和迁移体系。
- 创建和删除使用临时目录加原子 rename，防止留下半写资源。
- 请求开始时把 metadata 和标准化 reference WAV bytes 解析为不可变 snapshot；
  后续编码不再依赖可能被删除的文件路径。

### 5.4 `StreamingSession`

每个 WebSocket 连接只保存：

- 固定的 voice、mode、style 和 generation options；
- 一个 `Segmenter` 和 segment queue；
- 一个单调递增的已接受文本 UTF-8 byte 计数；
- 当前唯一的 `SpeechService` PCM stream 和消费它的 task；
- cancellation signal，以及由 session `run` 独占写入的协议状态。

`StreamingSession` 不保存 Nano generator、Nano task、continuation text 或
generated latents；这些均由 `SpeechService` 在其 stream 内拥有和清理。

不保存客户端播放位置，不提供 session 恢复。

## 6. 音色管理

### 6.1 存储

```text
data/voices/<voice-id>/
├── metadata.json
└── reference.wav
```

`metadata.json` 只包含：

```json
{
  "id": "voice_01k...",
  "name": "assistant",
  "prompt_text": "可选的精确参考音频文本",
  "duration_seconds": 8.4,
  "created_at": "2026-07-27T00:00:00Z"
}
```

不保存：

- candidate；
- version；
- rights assertion；
- generated artifact；
- persisted latent；
- model fingerprint；
- audit history。

### 6.2 创建

```http
POST /v1/voices
Content-Type: multipart/form-data
```

字段：

| 字段 | 必填 | 说明 |
|---|---:|---|
| `name` | 是 | 人类可读名称 |
| `file` | 是 | WAV、FLAC 或 MP3 reference audio |
| `prompt_text` | 否 | 与音频逐字一致时启用 faithful 模式 |

服务在注册时：

1. 解码并 downmix 为 mono；
2. 重采样为 VoxCPM2 encoder 所需的 16 kHz；
3. 检查非空、非静音和时长；
4. 保存为标准 WAV；
5. 返回 voice metadata。

参考音频推荐 5～30 秒。首版接受范围固定为 3～60 秒，超过范围直接拒绝。
不提供远程 URL 或服务器本地路径注册方式。

### 6.3 查询和删除

```text
GET    /v1/voices
DELETE /v1/voices/{id}
```

- list 返回 metadata，不返回 reference audio。
- 名称可重复，调用始终使用唯一 ID。
- update 不进入首版；需要修改时删除后重新创建。
- delete 删除目录并清理该 voice 的进程内 latent cache。
- 正在使用的请求/session 保持开始时取得的不可变 snapshot 直到结束；delete
  原子地使新查询看不到该 ID，再删除目录和 cache，不影响已有 snapshot。

本项目使用可信数据并运行在可信内网，Voice Profile 安全和授权治理不在范围内。

## 7. Canonical 合成选项

HTTP body 和 WebSocket `start` 共用一个 `SynthesisOptions`：

```json
{
  "voice": {
    "type": "profile",
    "id": "voice_01k..."
  },
  "mode": "controllable",
  "style": "温暖、自然、语速稍慢"
}
```

`voice` 是可选 union：

```json
{"type":"profile","id":"voice_01k..."}
```

或：

```json
{
  "type": "design",
  "description": "温暖自然的年轻女性声音，音高略低"
}
```

字段规则：

| 字段 | 规则 |
|---|---|
| `voice` | 可选；省略表示普通无参考合成 |
| `mode` | 只允许 profile 使用；省略时为 `controllable`，也可显式使用 `faithful` |
| `style` | 可选自然语言 instruction；faithful 禁止 |

确定性校验规则：

- 无 `voice` 或 `voice.type=design` 时出现 `mode`，返回 `invalid_request`。
- profile 的 `faithful` 要求该 profile 有非空 exact transcript，否则返回
  `invalid_request`。
- `faithful + style` 返回 `invalid_request`。
- design 必须有非空 `description`；profile 必须有存在的 `id`。

HTTP body 是 `text + SynthesisOptions`；WebSocket `start` 只包含
`SynthesisOptions`，文本由后续 `append` 提供。

`cfg_value`、`temperature` 和 `inference_timesteps` 在 Phase 0 固定为服务内部
默认值，不进入公共 API。`max_generate_length` 由 `VoxCPMEngine` 使用与 Nano
相同的 tokenizer 对本次实际完整 target text 计数；首段 control prefix 也参与
计数。公式固定为 `min(target_token_count * 6 + 10, 2000)`。

服务只接受已知字段，unknown field 返回 `invalid_request`。

## 8. HTTP 非流式 API

### 8.1 健康检查

```http
GET /health
```

ready 时：

```json
{
  "status": "ready",
  "cuda": true,
  "model": "openbmb/VoxCPM2",
  "sample_rate": 48000
}
```

服务只在 CUDA preflight、模型加载和 warmup 成功后开始接受请求。正常运行时
`/health` 返回 ready `200`；runtime fatal 一经发现就撤销 ready 并开始有界
停服，在退出窗口不得继续返回 ready。preflight 或模型加载失败时记录稳定错误码
并退出，不启动一个长期返回 `503` 的后台状态服务。deploy 脚本通过容器退出或
health timeout 判断启动失败。

Nano `wait_for_ready()` 不包含首次 generation compile；warmup 必须实际完成一条
固定短文本生成并丢弃结果，不能只等待 worker ready。

### 8.2 合成

```http
POST /v1/speech
Content-Type: application/json
Accept: audio/wav
```

响应固定为：

```text
Content-Type: audio/wav
```

服务使用同一分段器处理完整文本，并按顺序收集 `SpeechService` 的 PCM
iterator，最后封装成一个 48 kHz mono WAV。它不创建 job、artifact 或下载 URL。

### 8.3 鉴权

`/health` 不鉴权。`/v1/*` 和 WebSocket 使用一个部署级 Bearer Token：

```http
Authorization: Bearer <BOTIFIED_TTS_API_KEY>
```

这是内部服务的最小访问边界，不增加账号、角色、租户或审计体系。

## 9. WebSocket 双向流式 API

### 9.1 入口

```text
WS /v1/speech/stream
```

连接建立后，第一条消息必须是 `start`：

```json
{
  "type": "start",
  "voice": {"type":"profile","id":"voice_01k..."},
  "mode": "controllable",
  "style": "自然、亲切"
}
```

服务校验后返回：

```json
{
  "type": "ready",
  "audio": {
    "encoding": "pcm_s16le",
    "sample_rate": 48000,
    "channels": 1
  }
}
```

### 9.2 客户端消息

追加任意粒度文本：

```json
{"type":"append","text":"我感觉"}
{"type":"append","text":"这个方案可以，"}
{"type":"append","text":"我们继续吧。"}
```

强制提交当前 buffer，但保持 session：

```json
{"type":"flush"}
```

提交尾部并等待全部音频发送完成：

```json
{"type":"finish"}
```

取消当前生成并结束 session：

```json
{"type":"cancel"}
```

`append` 可以在服务生成和发送前一 segment 音频时继续到达，这就是产品要求的
双向流式。

每个连接的 `run` 在同一个 structured-concurrency scope 内监督两个并发 worker：

```text
receive task
  -> 接收 append/flush/finish/cancel
  -> 独占驱动 segmenter 和 800 ms absolute deadline
  -> 将不可变 segment 放入 queue

generate/send task
  -> 从 queue 串行取 segment
  -> 消费当前唯一的 SpeechService PCM stream
  -> 将每个 PCM chunk 直接发送
```

两者共享一个 cancellation signal。没有独立 audio queue，也没有第三个持久
timer task 与 receive task 并发访问 `Segmenter`。

### 9.3 服务端消息

服务端只发送：

- `ready` JSON；
- 有序 WebSocket binary PCM message；
- `done` JSON；
- `error` JSON。

完成：

```json
{"type":"done","cancelled":false}
```

错误：

```json
{
  "type": "error",
  "error": {
    "code": "invalid_request",
    "message": "faithful mode does not accept style"
  }
}
```

WebSocket 本身保证 message 顺序，因此不增加 audio sequence、自定义 binary
header、sample offset 或 ack。

### 9.4 状态机

```text
NEW --start--> ACTIVE --finish--> DRAINING --> DONE
                  |
                  +--cancel----------------> CANCELLED

任意状态 --protocol/engine error----------> ERROR
```

`ACTIVE` 中可以正在生成，也可以只等待客户端消息；两种情况使用同一个 idle
规则。voice、mode 和 style 在 `start` 后固定；需要改变时建立新连接。

连接建立后 60 秒内必须收到有效的首个 `start`；这是 `NEW` 阶段的 handshake
deadline。业务 idle 只在 `ACTIVE` 生效，从最后一条被服务接受的有效客户端消息
开始计算 60 秒；服务端发送 `ready`、PCM 或其他消息都不续期。`finish` 进入
`DRAINING` 后禁用 idle deadline，让已经接受的文本正常生成和发送完成。
`ACTIVE` idle 到期与 `cancel` 使用同一路径，最终返回
`done(cancelled=true)`；首个 `start` 超时也按同一取消结果结束。

session `run` 是终态转换的唯一 owner：receive task、generate/send task 和
deadline 只返回结果或设置 signal，不发送 terminal `done`/`error`，不关闭
socket，也不释放 admission slot。`run` 汇总结果后只选择一个终态，至多发送一个
terminal event，然后关闭连接并在 `finally` 中释放已经取得的 admission slot。

cancel：

1. 停止接收新文本；
2. 关闭当前 `SpeechService` stream，由它取消底层 Nano request；
3. 清空尚未生成的 segments 并阻止后续 PCM send；
4. 由 `run` 返回 `done(cancelled=true)` 并关闭连接。

已经进入 socket write 的一个 frame 可能无法撤回。服务不尝试判断客户端已播放
到哪里。Botified barge-in 后的新回答使用新连接。

### 9.5 音频 chunk

- PCM s16le；
- 48 kHz；
- mono；
- 固定 Nano/VoxCPM2 revision 的每个 waveform chunk（包括最后一步）都是
  7680 samples，即 160 ms；
- 每个 waveform chunk 转换后直接对应一个 WebSocket binary message，不合并、
  不切分，也不裁剪最后一步可能包含的自然尾音或静音；
- 因此固定输出频率为每秒音频 6.25 个 binary message，低于 10 个上限。

waveform 到 PCM 的唯一转换为：拒绝 NaN/Inf，再执行
`np.rint(np.clip(waveform, -1.0, 1.0) * 32767.0).astype("<i2")`。服务不做响度
归一化、fade、重采样或第二次声道处理。HTTP WAV 和 WebSocket PCM 复用这一个
转换函数。固定边界向量 `[-2, -1, -.5, 0, .5, 1, 2]` 的结果必须是
`[-32767, -32767, -16384, 0, 16384, 32767, 32767]`，与
libsndfile/soundfile 默认 PCM_16 写入语义一致。

该约束是按“音频时长”计算，不要求服务器按墙钟时间定时发送。

generate/send task 直接 await WebSocket send。发送超过 5 秒时取消 Nano，
并把 `client_too_slow` 结果交给 `run`；由 `run` best-effort 发送 error，然后
关闭连接。协议不承诺已经不可写的客户端一定能收到该 error。

服务在接受 append 前，以
`accepted_utf8_bytes + len(text.encode("utf-8"))` 检查 session 累计文本上限。
计数在 append 被接受后单调增加，文本进入 queue、开始生成或生成完成都不返还
预算。同一份文本只计数一次；queue 和未切分 buffer 只是已接受文本的两个去向，
不再相加形成第二个 session 预算。一旦 append 被接受，它产生的所有 segments
都可以进入 queue，不会因为句子数量较多而中途拒绝。

## 10. 文本分段

### 10.1 目标

同一个增量分段器必须正确处理：

- 单字或逐词 append；
- 随机小 chunks；
- 一次包含多句的大 chunk；
- HTTP 完整长文本；
- 中英文和中英混合；
- 非语言标签、数字和小数。

无论输入 chunk 如何切分，送入模型的 spoken text 都不得丢字、重字或重排。

### 10.2 唯一分段策略

每次 append 后循环提取零到多个 segment：

1. 优先在 `。！？!?` 后切分；`.` 只有确认不是数字小数点时才是强边界。
2. digit 后且位于 buffer 尾部的 `.` 暂不提交；下一字符是 digit 时作为小数，
   否则再把该 `.` 作为句末。
3. 缓存达到软切分最小长度后，遇到 `，,；;：:` 或空格即可切分。
4. buffer 达到目标最大字符数时，优先取不超过目标值的最后一个安全软边界；
   没有安全边界则继续缓存到下一个强边界或 hard maximum。
5. 达到 latency deadline 时，从最近的安全软边界切分；没有软边界且已达到
   deadline 兜底最小长度时，从当前安全位置提交。
6. 达到 hard maximum 时，从不超过 hard maximum 的最后安全位置强制切分；
   没有标点时允许直接按字符位置切分；异常超长的连续 decimal 也允许在 digit
   之间切分。
7. 官方 tag 的完整值及其跨 append 的可能前缀都是受保护区间。例如先收到
   `[laugh`、再收到 `ing]` 时不得在其中切分。一个 `[` 开头的内容一旦不可能
   匹配任何官方 tag，就按普通文本处理。
8. `flush` 和 `finish` 提交剩余文本；此时尾部 `.` 或未完成的 tag 前缀按普通
   文本处理。
9. hard maximum 优先于 decimal 和普通边界保护。完整官方 tag 及仍可能成立的
   官方 tag 前缀是唯一绝对不可切分的区间；必须在其之前切分，绝不切断 tag。

初始工程默认值：

| 常量 | 初始值 |
|---|---:|
| 软切分最小字符数 | 24 |
| deadline 兜底最小字符数 | 12 |
| 目标最大字符数 | 100 |
| hard maximum | 160 |
| latency deadline | 800 ms |

这些值在技术 spike 中用 Botified 的真实中英文输出固定一次，随后作为内部常量。
首版不把它们暴露成请求参数，也不提供多种 segment policy。

receive task 独占 `Segmenter`，并保存一个基于 monotonic clock 的绝对
`deadline_at`。buffer 第一次从空变为非空时令
`deadline_at = now + 800 ms`；后续 append 不重置。receive loop 每次等待下一条
客户端消息时都使用该绝对时刻计算剩余 timeout，因此持续收到消息也不能推迟
deadline。timeout 到期时由同一个 receive task 调用 segmenter：

- 已达到 12 字符则提交；
- 不足 12 字符则保留“deadline 已到期”状态，后续一旦达到阈值立即提交；
- segment 提交后若仍有 pending text，则从该时刻建立新的 absolute deadline；
  buffer 清空时清除 deadline，后续新文本重新开始；
- `flush`、`finish` 和 `cancel` 清除 deadline。

不创建独立的持久 timer task，也不允许 receive task 之外的 task 调用
`Segmenter`。

强句末标点、`flush` 或 `finish` 可以提交短段，因为短回答必须能够结束；服务不
通过填充无意义文本来绕过 VoxCPM2 对极短语音稳定性较弱的事实。

### 10.3 固定资源上限

| 资源 | 上限 | 超限行为 |
|---|---:|---|
| HTTP `text` | 8 KiB UTF-8 | `input_too_large` |
| WebSocket 单个 `append` | 16 KiB UTF-8 | `input_too_large` |
| WebSocket session 累计文本 | 64 KiB UTF-8 | `input_too_large` |
| 未切分 buffer | 4 KiB UTF-8 | 每次 append 后由 `Segmenter` 按 hard maximum 循环提取，天然保持有界 |
| `style` | 512 B UTF-8 | `invalid_request` |
| Voice Design `description` | 1 KiB UTF-8 | `invalid_request` |
| reference upload | 25 MiB | `invalid_request` |
| WebSocket idle | 60 秒 | 等价 cancel，`done(cancelled=true)` |
| WebSocket send | 5 秒 | `client_too_slow` 并结束 session |
| 同时接纳的 HTTP 请求/WS session | 16 | `service_busy` |

这些是内部固定稳定性边界，不作为配置项。HTTP 在处理 body 前、WebSocket 在
接受 `start` 时非阻塞获取同一个进程内 admission slot，并在请求/session 的
`finally` 中释放。没有 slot 时立即返回 `service_busy`；不把请求排进 Nano 的
无界 waiting queue。admission counter 与 Nano `max_num_seqs` 使用同一个常量，
但它只是入口资源边界，不实现第二个 scheduler。

未切分 buffer 不设置第二套 byte counter 或独立错误；`Segmenter.append()` 返回
时已经按上述 hard maximum 完成提取。

HTTP 的 `service_busy` 使用 `503` 并返回 `Retry-After: 1`；这是服务整体的瞬时
推理容量不可用，不是某个客户端超过 rate quota，因此不使用 `429`。WebSocket
在 `start` 时发送 `service_busy` error 后关闭。

### 10.4 HTTP 复用

HTTP 将完整文本一次 feed 给相同分段器，再调用 `finish`。它不使用另一套
“长文本 splitter”，也不把整篇长文本直接送入单次模型请求。

## 11. 跨段连续性

官方建议按句子生成流式语音，但独立生成每句可能造成音色、语速和边界不自然。
首版采用一种 continuation 方法：

1. segment N 完整生成后，保留它的 continuation text 和最终 generated latents。
2. segment N+1 使用二者作为 continuation prompt。
3. profile 模式同时继续提供原始 reference latent，约束音色身份。
4. 只保留上一个完整 segment，不维护 deque。
5. continuation 只存在于当前请求/session 内存中。
6. 新请求、新连接、cancel 或 error 都清空 continuation。

内部统一命名为 `continuation_text`：

- generated latents 只包含当前 segment 新生成的 latent，不包含 reference、
  历史 prompt 或其他 conditioning；
- `continuation_text` 是当前 segment 实际传给 Nano 的完整 model target text；
- 首段的 style/Voice Design control prefix，以及 `[laughing]`、`[Uhm]` 等原生
  tag 均按实际 target text 保留；
- `continuation_text` 只在 segment 正常完成后与 latents 一起 commit，之后不可
  修改。

下一段分别把它们作为 Nano `prompt_latents` 和 `prompt_text`。

Voice Design 和 controllable clone 的 style 只在首段作为显式 instruction 注入；
后续 segment 的新 target 不重复注入 prefix；prefix 只作为上一段真实生成历史
进入 prompt。faithful 模式始终不接受 style。

该选择与 VoxCPM2 官方 `merge_prompt_cache()` 原样累积完整 `new_text` 的语义
一致，也保证 Nano 的 `prompt_text` 与同次生成得到的 `prompt_latents` 来自同一
条件历史。RTX 4090 focused A/B 中，full-target 的 next segment 为 23 个音频块、
3.68 秒，边界 sample jump 约 `1.40e-6`，未出现重复音素、截断或异常静音；
spoken-only 没有可观察优势，分支已删除，不作为配置或备用路径保留。

不增加：

- playback ack；
- speculative/committed 双状态；
- revision；
- 回滚；
- crossfade；
- 断线恢复。

技术 spike 必须确认该方法相较每段只使用静态 reference 没有明显的重复音素、
截断、异常静音或持续语速漂移。验证通过后只保留 continuation 实现，不将两种
策略做成公开配置。

## 12. Nano-vLLM-VoxCPM 集成

### 12.1 选择 Nano

选择 Nano-vLLM-VoxCPM，而不是 vLLM-Omni，原因是：

- 产品只服务一个 VoxCPM2 模型；
- Nano 已提供 async streaming、并发 batching、reference/prompt latent 和
  cancel；
- 代码和运行边界更小，适合实现服务层的增量文本分段；
- 不需要 vLLM-Omni 的通用多模态 serving 面。

本项目不预留 vLLM-Omni adapter。

### 12.2 薄 fork 边界

当前 Nano 在 reference padding、continuation completion、AsyncPool cancel
传播、child fatal 传播和 runtime dependency 五处不能直接满足服务。因此
Botified 维护一个固定 commit 的最小 fork，只允许以下五项改动：

1. `encode_latents(audio, role=reference|prompt)` 使用正确 padding。
2. payload 使用独立的 `generated_latents` list 逐 step 保存新 latent；正常结束
   的 terminal completion 聚合并返回仅属于当前 segment 的 latents。
3. `AsyncVoxCPM2ServerPool.generate()` 显式持有 inner generator，并在 outer
   `finally` 中 `await inner.aclose()`，确保 SpeechService 关闭 outer stream 时
   cancel 到达实际 child request。
4. child `srv.step()` exception 发出 fatal event 后退出；parent 同时监视 child
   意外退出。两条路径都设置唯一 fatal state，使 active streams、pending
   operations 和后续 submit 有界失败。AsyncServer 使用同一个 sticky
   `asyncio.Event` 公开 `wait_for_fatal() -> NoReturn`：fatal 前等待，fatal 后
   立即抛出保存的 `RuntimeError`；normal stop 不触发。AsyncPool 公开同名接口，
   使用 `FIRST_COMPLETED` 等待任一 child fatal，并清理其余 waiter。
5. 从 fork `pyproject.toml` 删除 runtime/tests/deployment 均未使用的
   `torchcodec`，并删除 `.github/workflows/ci.yml` 中只为它存在的 version
   字段、安装步骤和 matrix 维度。

不能从 payload 的 `feats` 切片推导生成结果：sequence 经 preemption 后重新
prefill 时 Nano 会 concatenate `feats`，prompt/generated 边界会丢失。terminal
completion 必须在最后一个 waveform chunk 之后到达；cancel 和 error 不返回
completion。

Nano cancel 在当前 engine step 结束后的命令处理边界生效即可接受，不增加 step
内抢占。服务提前结束消费时必须 `aclose()` Nano generator；AsyncPool 修复后
必须重新用真实 GPU 验证 cancel 已到达 child 并释放 request。

fatal 不是 completion 或可恢复的 stream event。Nano 让 async generator 抛出
`RuntimeError`；服务对当前客户端 best-effort 映射 `engine_error` 后进入 fatal
状态并非零退出，由容器 restart policy 重启。Nano 内不 restart worker，不做
retry、fallback 或故障迁移。宿主只 await 公开的 `wait_for_fatal()`；不轮询
health，不读取 `_fatal_error`、`recv_task` 或 `servers`，不增加 callback。

fork 的 focused tests 只证明本项目新增的边界行为：关闭 AsyncPool outer stream
确实关闭 inner stream；step exception/child exit 会使 active、pending、后续
submit 和 fatal waiter 全部有界失败。waiter 同时覆盖 fatal 前等待、fatal 后
立即失败、AsyncPool 任一 child fatal 和 normal stop 不误报。cancel 的实际
child 释放和 idle child kill 只在同一份真实 GPU 部署 smoke 中各验证一次，不再
复制等价测试。

fork 不包含：

- Sync server/pool 修改；
- HTTP/WebSocket DTO；
- VoiceStore；
- 文本分段；
- 音频 codec；
- 产品错误码；
- session 状态；
- completion shape/dtype metadata；latents 在固定模型链路内作为 opaque bytes
  原样回传；
- 多 GPU 调度；
- artifact 或持久化。

不为已删除的 torchcodec 增加“缺失测试”、NPP 系统包、`LD_LIBRARY_PATH`、
optional extra 或备用解码路径。系统 FFmpeg 是 Botified `VoiceStore` 解码并
标准化 WAV/FLAC/MP3 的真实依赖，继续由项目 Dockerfile 安装并固定版本。

上游合并等价能力后，原地删除对应 fork 改动，不保留双路径。

### 12.3 固定依赖

- Python 包、PyTorch、FlashAttention 和 Nano Git dependency 固定在
  `pyproject.toml`/`uv.lock`。
- Linux x86_64 容器固定使用
  `nvidia/cuda:12.6.3-runtime-ubuntu24.04@sha256:2c8193530ecc423e0f123d0c85b68a15d1395adcddabfc943e2523dbfde172e1`。
  Dockerfile 固定 Linux x86_64
  `uv==0.10.8@sha256:f99c19c9683591761e0dc9d80db421b17d8c004adf4ac4031cac1fc92777f091`，
  由 uv 安装 Python 3.12.13，并固定 Ubuntu FFmpeg 包
  `7:6.1.1-3ubuntu5`。两个 `uv sync` 使用 300 秒 HTTP timeout，并复用
  BuildKit `/root/.cache/uv` cache mount，避免大 wheel 超时、重复下载或缓存
  固化进镜像层。
- runtime 镜像保留 Triton JIT 所需的 `gcc` 和 `libc6-dev`；当前依赖均使用
  已固定 wheel，不使用 CUDA devel 镜像，不安装 nvcc。
- Nano 使用不可变 git commit。
- Nano Git dependency 指向 Botified 最小 fork 的不可变 commit，不做运行时
  monkey patch，也不把整个 Nano 源码复制进本仓库。
- 当前 Nano 的未约束传递依赖会让解析器先选与 numba 不兼容的 NumPy。项目在
  顶层 `pyproject.toml` 固定 `numpy==2.4.6`、`numba==0.66.0` 和
  `llvmlite==0.48.0`，再生成唯一 `uv.lock`；不依赖解析器偶然回退。
- `pyproject.toml` 和 `uv.lock` 不包含未使用的 torchcodec。
- Hatch 仅为当前固定的 FlashAttention wheel direct reference 启用
  `allow-direct-references`，不增加第二套依赖安装路径。
- VoxCPM2 使用不可变 Hugging Face revision。容器通过 CUDA preflight 后，应用
  调用 `snapshot_download(repo_id, revision=<immutable-sha>,
  cache_dir=/data/model-cache)`，再把返回的本地 snapshot path 传给 Nano。
  不把 repo ID 直接传给 Nano，因为当前 Nano 的下载路径不接受 revision。
- 运行时不得 `pip install -U`。
- 不建设逐文件 hash、processor fingerprint 或 release attestation 平台。
- 每次升级只运行本项目的 focused GPU smoke 和 Botified 端到端验收。

## 13. CUDA 与一键部署

### 13.1 支持环境

首版只支持：

- Linux x86_64；
- CUDA 可见且所选 device 有效的 NVIDIA GPU；
- CUDA 12.x 兼容驱动；
- Docker、Docker Compose 和 NVIDIA Container Toolkit；
- 单个可见 GPU。

不支持 CPU fallback、Apple Silicon、Windows 或 ROCm。

当前 verified baseline 为 RTX 4090 24,564 MiB、compute capability 8.9、driver
575.64.05（driver CUDA capability 12.9）。隔离运行环境为 Python 3.12.13、
PyTorch/torchaudio 2.9.0+cu126、Triton 3.5.0、FlashAttention 2.8.3，以及第
12.3 节固定的 CUDA 12.6.3 runtime、uv 0.10.8、FFmpeg 和
NumPy/Numba/llvmlite；模型 revision 为
`bffb3df5a29440629464e5e839f4d214c8714c3d`。

该环境 warm generation 的 TTFB 为 0.1408 秒、生成阶段 RTF 为 0.1130；
`wait_for_ready()` 约 17.89 秒后首次 generation 仍有约 12.25 秒 compile，因此
ready 前必须执行真实生成 warmup。`gpu_memory_utilization=0.8` 时观测峰值显存
18,849 MiB。以上只证明当前 4090 配置，不代表最低显存或最低 compute
capability。其他 CUDA GPU 可以启动尝试但标记为未验证；部署脚本不根据未经实测
的硬件阈值拒绝设备。

### 13.2 CUDA fail-fast

`./scripts/deploy.sh` 在构建镜像/启动前检查：

1. `docker`；
2. `docker compose`；
3. `nvidia-smi`；
4. 所选 GPU device 是否存在，并输出型号、显存和 compute capability 供故障
   定位。

host preflight 检查 `HOST_GPU` 指定的宿主物理设备；Compose 只把该设备暴露给
容器，所以下面的容器 preflight 固定检查逻辑 device 0。

应用镜像构建完成后，容器 entrypoint 在 import/初始化 Nano 和下载 VoxCPM2
权重前检查 Docker 内 GPU 可见性：

```python
torch.cuda.is_available()
torch.cuda.device_count()
selected_device_is_valid
```

失败时：

- 输出 `cuda_unavailable` 或 `cuda_device_invalid`；
- 非零退出；
- 不下载模型；
- 不创建 Nano worker；
- 不尝试 CPU。

### 13.3 唯一部署方式

```bash
./scripts/deploy.sh
```

脚本：

1. 完成 CUDA preflight；
2. 自动生成或读取权限为 `0600` 的 `deploy/.env`；文件或 API key 不存在时
   生成 32-byte 随机 ASCII key，不要求用户预先提供；
3. 使用固定 base image digest 的 Dockerfile 执行 `docker compose build`；
4. `docker compose up -d --wait --wait-timeout 900`；
5. 通过容器 healthcheck 等待 `/health` ready；healthcheck 的 interval 和
   timeout 均为 5 秒，最多重试 180 次；
6. 在 180 秒 timeout 内生成一条固定短文本 WAV 作为 smoke。

host CUDA preflight 失败时不开始 build。Docker 内 GPU 检测失败时不得下载
VoxCPM2 模型权重。health timeout 或容器退出时，脚本输出容器状态和查看日志的
命令并非零退出。

Compose 挂载 `/data` 持久卷：

```text
/data/voices
/data/model-cache
```

容器重建不重复下载模型或丢失注册音色。镜像包含 reference WAV/FLAC/MP3
解码所需的 FFmpeg。Compose 使用 NVIDIA `device_ids` 只暴露 `HOST_GPU`
选择的一个宿主 GPU，使用 named volume 挂载 `/data`，并保留
`restart: on-failure`；不增加容器内 worker restart。

不同时维护 systemd、Podman、裸机 pip 和 Kubernetes 安装器。

首版不依赖尚未定义的镜像 registry、发布 owner 或应用镜像 digest。未来若明确
要求发布预构建镜像，原地替换唯一部署路径，不与本地 build 长期并存。

### 13.4 配置

应用进程仍且只读取以下八个环境变量：

```text
BOTIFIED_TTS_HOST=0.0.0.0
BOTIFIED_TTS_PORT=8000
BOTIFIED_TTS_MODEL=openbmb/VoxCPM2
BOTIFIED_TTS_MODEL_REVISION=<immutable-revision>
BOTIFIED_TTS_GPU_DEVICE=0
BOTIFIED_TTS_DATA_DIR=/data
BOTIFIED_TTS_API_KEY=<secret>
BOTIFIED_TTS_LOG_LEVEL=INFO
```

`deploy/.env` 另允许两个只供 deploy script 和 Compose 插值的部署变量：

```text
HOST_GPU=0
PUBLISHED_PORT=8000
```

它们不注入应用容器，也不使用 `BOTIFIED_TTS_` 前缀。Compose 用 `HOST_GPU`
选择宿主物理 GPU、用 `PUBLISHED_PORT` 发布服务；容器只看到所选 GPU，因此固定
`BOTIFIED_TTS_GPU_DEVICE=0`，并固定监听 `BOTIFIED_TTS_PORT=8000`。其他应用
配置仍通过上列已有变量传入，不增加 host GPU 或 published port 的应用变量。

不同时维护 YAML 和第二套 service config。Nano 内部调优值保留为代码中的已验证
默认值，只有出现真实部署调优需求后才开放配置。

## 14. Botified 与 Agent Skill

### 14.1 Botified 集成边界

Botified 负责：

- 将 Agent 输出转换成应朗读的纯文本；
- 把 LLM text delta 依次发送到 WebSocket `append`；
- 回答结束时发送 `finish`；
- 用户打断时发送 `cancel`；
- 播放 PCM 或将 HTTP WAV 交给现有文件/渠道路径；
- 需要 Ogg/Opus 时在渠道边界转码。

TTS 服务不实现独立 bridge，不修改 Botified Gateway，不管理渠道发布。

职责划分：

- `botified-tts` 仓库负责协议、服务、示例 WebSocket client 和 Skill。
- 相邻 `botified` 仓库负责把其 LLM text preview/delta 接到本 WebSocket，并把
  PCM 交给现有播放或媒体路径；该工作由 Botified runtime 团队完成。
- 跨仓库端到端通过是 Botified TTS release dependency，不是本仓库单独可关闭
  的代码任务。

### 14.2 Agent Skill

仓库提供：

```text
skills/botified-tts/
├── SKILL.md
└── scripts/botified-tts
```

薄 helper 只提供：

```text
health
voice-create
voice-list
voice-delete
speak
```

`speak` 可以使用普通、Voice Design、controllable clone 和 faithful clone，并把
WAV 写到显式输出路径。helper 复用公开 HTTP API，不实现第二套 TTS pipeline。

helper 只读取：

```text
BOTIFIED_TTS_URL
BOTIFIED_TTS_API_KEY
```

不增加 client config 文件。

Skill 用于 Agent 显式生成语音文件。它不能接管同一次 LLM 回答的逐 token
stream；实时朗读由 Botified runtime 直接调用 WebSocket。

## 15. 错误与日志

### 15.1 稳定错误码

首版只保留：

```text
invalid_api_key
invalid_request
invalid_voice
input_too_large
service_busy
cuda_unavailable
cuda_device_invalid
model_load_failed
engine_error
client_too_slow
```

REST 返回 JSON error envelope；WebSocket 使用同样的 code/message 放进
`error` event。cancel 是正常的 `done(cancelled=true)`，不是错误码。内部异常
不向客户端返回 traceback。

HTTP 状态固定为：

| 结果 | HTTP status |
|---|---:|
| speech、voice list、health 成功 | 200 |
| voice create 成功 | 201 |
| voice delete 成功 | 204 |
| `invalid_api_key` | 401 |
| `invalid_request` | 400 |
| `invalid_voice` | 404 |
| `input_too_large` | 413 |
| `service_busy` | 503 |
| `engine_error` | 500 |

框架 schema validation 也统一映射为 `invalid_request` 400，不泄漏 FastAPI 的
默认错误格式。`cuda_unavailable`、`cuda_device_invalid` 和
`model_load_failed` 是启动日志/进程退出码，不会由已 ready 的 HTTP 服务返回。
ready 后的推理 fatal 对当前请求 best-effort 映射为 `engine_error`，随后顶层
runner 非零退出；不通过 traceback 字符串猜测独立 OOM 错误码。
`client_too_slow` 只用于 WebSocket。

### 15.2 最小可观测性

进程 ready 时记录一次启动日志，fatal 时记录一次致命日志。每个 HTTP synthesis
或 WebSocket session 只记录一条 terminal summary：

- request/session ID、voice type 和 mode；
- accepted chars 和 segment 数；
- TTFB、audio duration 和 RTF；
- result/error code。

TTFB 是从 HTTP 请求被接纳或 WebSocket 首个文本 append 被接受，到首个音频
chunk 可发送的 monotonic elapsed time。RTF 是所有 segment 的推理 wall time
之和除以生成音频总时长。请求在生成音频前结束时，TTFB、audio duration 和 RTF
均为 `null`。

默认不记录：

- 朗读正文；
- style/description；
- reference audio 和 transcript；
- generated audio；
- latent；
- API key；
- 原始上游异常和 traceback。

该摘要直接保存在请求/session 局部状态中，不增加 observer、event 或 metrics
层。首版不建设 metrics endpoint 和 dashboard。出现实际运维需求后再增加。

## 16. 测试与验收

测试只验证本项目拥有的行为，不重复测试 PyTorch、CUDA、FastAPI 或 Nano 的
内部测试。

### 16.1 单元测试

- 分段器：逐字、小 chunk、大 chunk、多句、deadline、flush、finish、100/160
  字符边界，以及跨 append 的尾部数字 `.` 和官方 tag 前缀。
- 请求 union、mode/style 约束和固定 HTTP status 映射。
- VoiceStore 创建、list、delete、半写失败清理，以及 delete 与已取得 immutable
  snapshot 并发时已有请求仍可读取。
- PCM 对固定 float vectors 执行 NaN/Inf 拒绝、clip、round 和 little-endian
  int16 转换，并验证每个 7680-sample Nano chunk 直接映射一个 binary message。
- `SpeechService` 只在 terminal completion 后 commit continuation，并在正常、
  cancel 和 error 路径显式 `aclose()` Nano stream。
- admission slot 满时立即 `service_busy`，所有 HTTP/WS 结束路径都释放 slot。
- 单个 append、累计已接受文本和未切分 buffer 的各自上限触发固定错误；累计文本
  不因进入 queue 或完成生成而重复计数或返还，所有终态都释放 session。
- CUDA preflight 失败时模型下载/加载函数没有被调用。

Nano fork 的 focused unit tests 覆盖 `wait_for_fatal()` 的 wait-before-fatal、
fatal-before-wait、AsyncPool 任一 child fatal，以及 normal stop 不触发 fatal。
不在本项目重复模拟 Nano queue、process watcher 或 scheduler。

### 16.2 API 集成测试

使用一个最小 fake Nano adapter，只验证本项目协议：

- HTTP request 经 canonical `SpeechService` 返回合法 WAV。
- WebSocket `start -> append -> binary audio -> finish -> done`。
- 生成期间仍可接收 append。
- cancel 停止当前任务并清空队列。
- 慢客户端触发有界失败而不是无限缓存。
- idle 状态下 fake Nano fatal 无需下一请求即可使顶层 runner 撤销 ready 并抛错；
  正常 shutdown 取消 waiter 并正常返回。

fake 不模拟 Nano scheduler、KV cache、FlashAttention 或 CUDA OOM 的内部过程。

### 16.3 真实 GPU smoke

一个脚本在目标 GPU 上依次覆盖：

1. CUDA 检测、模型加载和 warmup；
2. 普通 TTS；
3. Voice Design；
4. controllable reference clone + style；
5. faithful clone；
6. 非语言标签；
7. WebSocket 增量文本；
8. 两个以上 segment 的 continuation；
9. AsyncPool outer `aclose()` 后 cancel 到达 child，且下一请求正常；
10. 服务 idle 时终止 Nano child，无需下一请求即撤销 ready 并非零退出；
11. RTF 小于 1；
12. 每个音频 chunk 均为 7680 samples，输出频率为 6.25/s。

不建设 8/16/32 并发矩阵、全语言矩阵、自动 WER/UTMOS 平台或 worker restart
仿真。

### 16.4 人工听测

使用一套固定样本，覆盖：

- 普通话；
- 英语；
- 中英混合；
- Voice Design；
- 两种 clone；
- style 和 `[laughing]`/`[sigh]`/`[Uhm]`；
- 逐字输入与大 chunk 输入；
- 至少四个连续 segments。

只验收业务可感知问题：

- 音色是否稳定；
- style 是否明显；
- faithful 是否接近 reference；
- 是否丢字、重字；
- 边界是否出现 click、异常静音、重复/截断音素；
- 长回答是否明显加速或漂移。

## 17. 仓库结构

```text
botified-tts/
├── README.md
├── pyproject.toml
├── uv.lock
├── src/botified_tts/
│   ├── app.py
│   ├── runtime.py
│   ├── streaming.py
│   ├── config.py
│   ├── schemas.py
│   ├── speech.py
│   ├── engine.py
│   ├── voices.py
│   ├── segmenter.py
│   └── audio.py
├── tests/
│   ├── test_segmenter.py
│   ├── test_speech.py
│   ├── test_api.py
│   ├── test_runtime.py
│   ├── test_streaming.py
│   └── gpu_smoke.py
├── deploy/
│   ├── Dockerfile
│   └── compose.yaml
├── scripts/
│   └── deploy.sh
├── skills/botified-tts/
│   ├── SKILL.md
│   └── scripts/botified-tts
└── docs/engineering/
    └── botified-tts-product-development-plan.md
```

不要预先创建 repository/service/use-case/domain 多层目录。只有文件真实变大并
出现明确职责边界时才拆分。

## 18. 开发阶段

### Phase 0：推理技术 Spike

目标是在写完整服务前消除唯一高风险问题。

交付：

- 将 VoxCPM2 HF revision、Nano commit、容器 base digest、Python、PyTorch、
  CUDA、FlashAttention 和 FFmpeg 版本固定到实际交付文件；
- 写回 RTX 4090 24 GiB、compute capability 8.9 和实测软件组合的 verified
  support baseline，不推断最低硬件；
- target GPU 上完成普通、design、两种 clone 和 style；
- 验证官方 non-verbal tag；
- 实现并验证 role-aware latent encoding；
- 让 Nano terminal completion 返回独立聚合的本 segment generated latents；
- 使用第 11 节已验证的 full model target continuation，不保留 spoken-only
  分支；
- 修复 AsyncPool outer→inner `aclose()` 后重新验证 cancel 到达 child；
- 验证 step exception 和 child 意外退出均使 async 调用有界失败，并可由公开
  fatal waiter 在 idle 状态观测；
- 测得 TTFB、RTF、实际 audio chunk 时长和显存占用。

退出条件：

- reference GPU 上 RTF < 1；
- continuation 无明显质量倒退；
- controllable 与 faithful 模式行为和官方一致；
- outer `aclose()` 能停止实际 child request；
- child fatal 不留下悬挂 stream/future，active 或 idle 时服务随后均非零退出；
- 对 Nano fork 的改动不超出第 12.2 节。

如果 continuation 验证失败，先解决或明确模型限制，不用 crossfade、回滚状态机
或第二套策略掩盖。

### Phase 1：服务 V1

交付：

- canonical schema；
- VoiceStore；
- SpeechService 与 Nano adapter；
- 顶层 HTTP/fatal runtime supervisor；
- segmenter 和 continuation；
- `/health`、voice 创建/列表/删除、`POST /v1/speech`；
- WebSocket；
- WAV/PCM；
- 错误、日志和 focused tests。

退出条件：

- HTTP 和 WebSocket 共用同一核心；
- 任意 text chunk 粒度不丢字不重字；
- cancel 和慢客户端有界；
- idle Nano fatal 无需下一请求即可使 runner 失败；
- 单元与 API 集成测试通过。

### Phase 2：部署与 Botified 交付

交付：

- 固定依赖和模型 revision；
- Dockerfile、Compose 和 `deploy.sh`；
- CUDA 双重 fail-fast；
- README 和调用示例；
- Agent Skill；
- Botified text delta 到 WebSocket 的真实联调；
- 一次真实 GPU smoke 和固定样本听测。

退出条件：

- 满足支持表的 fresh CUDA host 一条命令 ready；
- 无 CUDA 时在模型下载前明确失败；
- `botified` runtime 团队完成一次真实回答的连续播放和 cancel；
- Definition of Done 全部满足。

## 19. 风险与处理

| 风险 | 最小处理 |
|---|---|
| Nano 上游差异 | 第 12.2 节固定五项薄 fork 改动 |
| 分段质量 | 上一完整段 continuation + 固定听测 |
| 资源积压 | 固定输入/队列上限、send timeout、cancel |
| 环境不兼容 | 支持表、host/container preflight、固定依赖 |

## 20. Definition of Done

- [ ] 无 CUDA 时在模型下载前非零退出且不尝试 CPU；支持表内 fresh host 可一键
  启动、ready 并完成 smoke。
- [ ] 音色可以创建、列出、删除；`/v1/speech` 返回可播放的 48 kHz mono WAV。
- [ ] 普通、Voice Design、controllable clone、faithful clone、style 和官方
  非语言标签均通过真实 GPU 验收。
- [ ] `faithful + style` 和所有超限请求得到固定错误。
- [ ] WebSocket 接受逐字、小 chunk 和多句大 chunk，并在 `finish` 前开始返回
  已形成 segment 的音频。
- [ ] 输入 chunk 边界不造成丢字、重字或重排。
- [ ] WebSocket 输出 48 kHz mono PCM s16le；固定验收语料平均每秒音频不超过
  10 个 binary chunks。
- [ ] continuation 使用上一段完整 model target text + generated latents，无明显
  click、异常静音、重复或截断音素。
- [ ] cancel 到达实际 Nano child；输入超限和 send timeout 均能有界结束
  session。
- [ ] Nano child fatal 使 active/pending 调用有界失败；即使服务 idle 且没有
  下一请求，也会撤销 ready 并非零退出；没有 worker restart 或 fallback。
- [ ] 默认日志不包含正文、reference、audio、latent 或 secret。
- [ ] 单元、API、同一份真实 GPU smoke 和固定样本听测通过。
- [ ] Agent Skill 可注册音色并生成 WAV。
- [ ] `botified` runtime 团队完成真实 token delta → 连续播放 → cancel 的
  release dependency 验收。
