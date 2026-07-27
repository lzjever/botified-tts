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
- 一个增量文本 buffer；
- 一个受 session 64 KiB 总文本预算约束的 segment queue；
- 当前 Nano generation task；
- 上一个完整生成段的 spoken text 和 generated latents；
- cancelled/finished 状态。

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
默认值，不进入公共 API。内部 `max_generate_length` 根据 segment 长度计算。

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

服务只在 CUDA preflight、模型加载和 warmup 成功后开始接受请求，因此
`/health` 只返回 ready `200`。preflight 或模型加载失败时记录稳定错误码并退出，
不启动一个专门返回 `503` 的后台状态服务。deploy 脚本通过容器退出或 health
timeout 判断启动失败。

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

每个连接固定使用两个并发任务：

```text
receive task
  -> 接收 append/flush/finish/cancel
  -> 驱动 segmenter 和 deadline timer
  -> 将不可变 segment 放入受 session 文本预算约束的 queue

generate/send task
  -> 从 queue 串行取 segment
  -> 调用 SpeechService/Nano
  -> 将 Nano waveform chunk 转换为 PCM 并直接发送
```

两者共享一个 cancellation signal。没有独立 audio queue。

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

`ACTIVE` 内部只有 `IDLE` 和 `GENERATING`。voice、mode 和 style 在 `start`
后固定；需要改变时建立新连接。

cancel：

1. 停止接收新文本；
2. 取消当前 Nano request；
3. 清空尚未生成的 segments 并阻止后续 PCM send；
4. 清空 continuation；
5. 返回 `done(cancelled=true)` 并关闭连接。

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
记录 `client_too_slow`，best-effort 发送 error，然后关闭连接。协议不承诺已经
不可写的客户端一定能收到该 error。

服务在接受 append 前检查 session 累计文本上限；一旦接受，该 append 产生的所有
segments 都可以进入 queue，不会因为句子数量较多而中途拒绝。queue 与尚未切分
buffer 的文本总量始终不超过 session 的 64 KiB 预算。

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
   没有标点时允许直接按字符位置切分。
7. 官方 tag 的完整值及其跨 append 的可能前缀都是受保护区间。例如先收到
   `[laugh`、再收到 `ing]` 时不得在其中切分。一个 `[` 开头的内容一旦不可能
   匹配任何官方 tag，就按普通文本处理。
8. `flush` 和 `finish` 提交剩余文本；此时尾部 `.` 或未完成的 tag 前缀按普通
   文本处理。
9. hard maximum 优先于普通边界选择，但必须先在受保护区间之前切分，不切断
   已识别或仍可能成立的官方 tag。

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

buffer 第一次从空变为非空时启动唯一一个 800 ms timer；后续 append 不重置
timer。到期时主动调用 segmenter：

- 已达到 12 字符则提交；
- 不足 12 字符则保留“deadline 已到期”状态，后续一旦达到阈值立即提交；
- segment 提交或 buffer 清空后，剩余新文本重新开始一次 deadline；
- `flush`、`finish` 和 `cancel` 取消 timer。

强句末标点、`flush` 或 `finish` 可以提交短段，因为短回答必须能够结束；服务不
通过填充无意义文本来绕过 VoxCPM2 对极短语音稳定性较弱的事实。

### 10.3 固定资源上限

| 资源 | 上限 | 超限行为 |
|---|---:|---|
| HTTP `text` | 8 KiB UTF-8 | `input_too_large` |
| WebSocket 单个 `append` | 16 KiB UTF-8 | `input_too_large` |
| WebSocket session 累计文本 | 64 KiB UTF-8 | `input_too_large` |
| 未切分 buffer | 4 KiB UTF-8 | 按 hard maximum 切分；仍无法切分则拒绝 |
| queued + 未切分文本 | 64 KiB UTF-8 session 总预算 | 接受 append 前检查 |
| `style` | 512 B UTF-8 | `invalid_request` |
| Voice Design `description` | 1 KiB UTF-8 | `invalid_request` |
| reference upload | 25 MiB | `invalid_request` |
| WebSocket idle | 60 秒 | 结束 session |
| WebSocket send | 5 秒 | `client_too_slow` 并结束 session |
| 同时接纳的 HTTP 请求/WS session | 16 | `service_busy` |

这些是内部固定稳定性边界，不作为配置项。HTTP 在处理 body 前、WebSocket 在
接受 `start` 时非阻塞获取同一个进程内 admission slot，并在请求/session 的
`finally` 中释放。没有 slot 时立即返回 `service_busy`；不把请求排进 Nano 的
无界 waiting queue。admission counter 与 Nano `max_num_seqs` 使用同一个常量，
但它只是入口资源边界，不实现第二个 scheduler。

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
- `continuation_text` 在 segment 正常完成后与 latents 一起 commit，之后不可
  修改；
- `[laughing]`、`[Uhm]` 等参与生成的原生文本表示保留；

下一段分别把它们作为 Nano `prompt_latents` 和 `prompt_text`。

Voice Design 和 controllable clone 的 style 只在首段作为显式 instruction 注入；
后续段通过前一段音频 continuation 延续表达风格。faithful 模式始终不接受
style。

VoxCPM2 官方 prompt-cache 路径会保留传入模型的完整 target text，因此静态代码
不能证明 `continuation_text` 应当只含 spoken segment，还是应包含首段未发声的
style/voice control prefix。Phase 0 只做一次 focused GPU A/B：

- A：`continuation_text = spoken segment`；
- B：`continuation_text = 该段实际传给模型的完整 target text`。

使用相同 seed 和至少四段固定语料比较丢字、重复/截断音素、音色及 style 漂移。
确定结果后删除失败分支，产品和配置中不暴露该选择。

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

当前审阅的 Nano 实现将 `encode_latents()` 统一做 left padding，而 VoxCPM2
官方实现对 isolated reference 使用 right padding、对 continuation prompt 使用
left padding；同时 Nano 的公开 generator 只返回 waveform，没有返回最终
generated latents。

因此 Botified 维护一个固定 commit 的最小 fork，只允许以下改动：

1. `encode_latents(audio, role=reference|prompt)` 使用正确 padding。
2. payload 使用独立的 `generated_latents` list 逐 step 保存新 latent；正常结束
   的 terminal completion 聚合并返回仅属于当前 segment 的 latents。

不能从 payload 的 `feats` 切片推导生成结果：sequence 经 preemption 后重新
prefill 时 Nano 会 concatenate `feats`，prompt/generated 边界会丢失。terminal
completion 必须在最后一个 waveform chunk 之后到达；cancel 和 error 不返回
completion。

先用 focused GPU test 验证上游 cancel。只有确认 sequence、KV 或请求状态未释放
时，才增加修复该缺陷所需的最小 patch。如果上游行为正确，不修改 cancel。上游
合并等价能力后删除对应 fork patch。

Nano cancel 在当前 engine step 结束后的命令处理边界生效即可接受，不增加 step
内抢占。服务提前结束消费时必须 `aclose()` Nano generator。

fork 不包含：

- HTTP/WebSocket DTO；
- VoiceStore；
- 文本分段；
- 音频 codec；
- 产品错误码；
- session 状态；
- 多 GPU 调度；
- artifact 或持久化。

### 12.3 固定依赖

- Python 包、PyTorch、FlashAttention 和 Nano Git dependency 固定在
  `pyproject.toml`/`uv.lock`。
- Python 系统版本、CUDA runtime、FFmpeg 和 OS 固定在 Dockerfile base image
  digest 与安装层。
- Nano 使用不可变 git commit。
- Nano Git dependency 指向 Botified 最小 fork 的不可变 commit，不做运行时
  monkey patch，也不把整个 Nano 源码复制进本仓库。
- 当前 Nano 的未约束传递依赖会让解析器先选与 numba 不兼容的 NumPy。项目在
  顶层 `pyproject.toml` 固定 `numpy==2.4.6`、`numba==0.66.0` 和
  `llvmlite==0.48.0`，再生成唯一 `uv.lock`；不依赖解析器偶然回退。
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

Phase 0 先记录当前可验证基线：RTX 4090 24 GiB、compute capability 8.9，以及
实测 driver、CUDA、PyTorch 组合。没有对应低规格 GPU 的真实 smoke，不声称最低
显存或最低 compute capability。`RTF < 1` 只承诺在表中指定的 reference GPU 上
成立，不泛化到所有能被 CUDA 识别的设备。其他 CUDA GPU 可以启动尝试，但标记为
未验证配置；部署脚本不根据未经实测的显存或 compute capability 阈值拒绝设备。

### 13.2 CUDA fail-fast

`./scripts/deploy.sh` 在构建镜像/启动前检查：

1. `docker`；
2. `docker compose`；
3. `nvidia-smi`；
4. 所选 GPU device 是否存在，并输出型号、显存和 compute capability 供故障
   定位。

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
2. 生成或读取权限为 `0600` 的 `deploy/.env`；
3. 使用固定 base image digest 的 Dockerfile 执行 `docker compose build`；
4. `docker compose up -d`；
5. 在有界 timeout 内等待 `/health` ready；
6. 生成一条固定短文本 WAV 作为 smoke。

host CUDA preflight 失败时不开始 build。Docker 内 GPU 检测失败时不得下载
VoxCPM2 模型权重。health timeout 或容器退出时，脚本输出容器状态和查看日志的
命令并非零退出。

Compose 挂载 `/data` 持久卷：

```text
/data/voices
/data/model-cache
```

容器重建不重复下载模型或丢失注册音色。镜像包含 reference WAV/FLAC/MP3
解码所需的 FFmpeg。

不同时维护 systemd、Podman、裸机 pip 和 Kubernetes 安装器。

首版不依赖尚未定义的镜像 registry、发布 owner 或应用镜像 digest。未来若明确
要求发布预构建镜像，原地替换唯一部署路径，不与本地 build 长期并存。

### 13.4 配置

仅使用环境变量：

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
engine_oom
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
| `service_busy`、`engine_oom` | 503 |
| `engine_error` | 500 |

框架 schema validation 也统一映射为 `invalid_request` 400，不泄漏 FastAPI 的
默认错误格式。`cuda_unavailable`、`cuda_device_invalid` 和
`model_load_failed` 是启动日志/进程退出码，不会由已 ready 的 HTTP 服务返回。
`client_too_slow` 只用于 WebSocket。

### 15.2 最小可观测性

结构化日志记录：

- request/session ID；
- voice type 和 mode；
- 输入字符数和 segment 数；
- queue wait；
- TTFB；
- audio duration；
- RTF；
- result/error code。

默认不记录：

- 朗读正文；
- style/description；
- reference audio 和 transcript；
- generated audio；
- latent；
- API key。

首版不建设 metrics endpoint 和 dashboard。出现实际运维需求后再增加。

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
- 输入、queue 和 session 上限触发固定错误并释放 session。
- CUDA preflight 失败时模型下载/加载函数没有被调用。

### 16.2 API 集成测试

使用一个最小 fake Nano adapter，只验证本项目协议：

- HTTP request 经 canonical `SpeechService` 返回合法 WAV。
- WebSocket `start -> append -> binary audio -> finish -> done`。
- 生成期间仍可接收 append。
- cancel 停止当前任务并清空队列。
- 慢客户端触发有界失败而不是无限缓存。

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
9. cancel；
10. RTF 小于 1；
11. 每个音频 chunk 均为 7680 samples，输出频率为 6.25/s。

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
- 对 continuation control prefix 做第 11 节唯一 A/B，删除失败分支后验证上一段
  continuation；
- 验证 cancel；
- 测得 TTFB、RTF、实际 audio chunk 时长和显存占用。

退出条件：

- reference GPU 上 RTF < 1；
- continuation 无明显质量倒退；
- controllable 与 faithful 模式行为和官方一致；
- cancel 能停止 request；
- 对 Nano fork 的改动不超出第 12.2 节。

如果 continuation 验证失败，先解决或明确模型限制，不用 crossfade、回滚状态机
或第二套策略掩盖。

### Phase 1：服务 V1

交付：

- canonical schema；
- VoiceStore；
- SpeechService 与 Nano adapter；
- segmenter 和 continuation；
- `/health`、voice 创建/列表/删除、`POST /v1/speech`；
- WebSocket；
- WAV/PCM；
- 错误、日志和 focused tests。

退出条件：

- HTTP 和 WebSocket 共用同一核心；
- 任意 text chunk 粒度不丢字不重字；
- cancel 和慢客户端有界；
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
| Nano 上游差异 | role-aware encode 与 completion result 两个最小 patch |
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
- [ ] continuation 无明显 click、异常静音、重复或截断音素。
- [ ] cancel、输入超限和 send timeout 均能有界结束 session。
- [ ] 默认日志不包含正文、reference、audio、latent 或 secret。
- [ ] 单元、API、同一份真实 GPU smoke 和固定样本听测通过。
- [ ] Agent Skill 可注册音色并生成 WAV。
- [ ] `botified` runtime 团队完成真实 token delta → 连续播放 → cancel 的
  release dependency 验收。
