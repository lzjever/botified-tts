# Botified TTS 产品开发计划

> 状态：后续产品改进已收敛，可交付开发
> 目标仓库：`botified-tts`
> 研究基线：2026-07-27
> 项目约束：`docs/development-constraints.md`；发生冲突时以项目约束为准
> 上游审阅基线：VoxCPM `616d3d3e630a9c96c2853250eef91b0f39dcd5fa`、
> Nano-vLLM-VoxCPM `0ef61b0ba634dbf2fad9e916bc4fb696a3c0f51f`

## 1. 产品定义

Botified TTS 是一个面向 Botified 的独立、轻量、CUDA-only TTS 服务。仓库同时
提供一个薄 Botified companion，把现有 Botified 文本流接到服务并在宿主机播放
PCM；它不是第二个服务或通用 bridge。

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
9. 普通用户使用公开的固定版本 CUDA 镜像、私有 env-file 和唯一
   `docker run`。
10. 提供本仓库内的最小 Botified companion。
11. 提供一个最小 Agent Skill。

## 2. 设计原则

### 2.1 KISS

- 一个服务部署单元。
- 一个只负责协议映射和本地播放的 Botified companion。
- 一个 VoxCPM2 模型。
- 一个服务实例使用一张 GPU。
- 一个合成核心同时服务 HTTP 和 WebSocket。
- 一个本地目录保存注册音色，不引入数据库。
- 一个生产启动命令：`docker run`。
- 仅支持当前 Botified 所需的纯文本输入和两种输出方式。
- companion 使用独立的轻量 Python 项目，不加入服务根 package 或根 uv
  workspace，也不安装 Torch/CUDA 依赖。

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
| 普通用户部署 | 固定版本 GHCR image + env-file + `docker run` |
| Power user 构建 | 根目录 Dockerfile |
| 开发者运行 | 根目录 `uv.lock` + `uv` |

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
- 公开的 Linux x86_64 CUDA 固定版本镜像。
- 私有 env-file 和唯一 `docker run` 生产启动方式。
- Botified 调用示例和最小 Agent Skill。
- 本仓库内消费 Botified `stream_text`、调用 WebSocket、播放 PCM 并转发
  barge-in cancel 的薄 companion。

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
- 通用或独立部署的 Botified TTS bridge 框架、渠道抽象和 daemon 平台。
- 多 GPU 调度、sticky routing 和故障迁移。
- LoRA 训练、在线加载或音色微调。
- Prometheus 平台、自动音质评分或大规模 benchmark 矩阵。
- Docker Compose、部署脚本、systemd、Kubernetes 和 Podman 安装器。
- 自动选择模型下载源、下载失败后的跨源 fallback。
- 把 VoxCPM2 权重打包进应用镜像。
- 多架构镜像和中国镜像 registry 同步。
- 项目许可证选择；这是用户或产品 owner 另行作出的法律决定，不分配给开发团队，
  本计划不作推荐。
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

`cfg_value`、`temperature` 和 `inference_timesteps` 在当前推理基线固定为服务内部
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
  "model": "VoxCPM2",
  "sample_rate": 48000
}
```

服务只在 CUDA preflight、模型加载和 warmup 成功后开始接受请求。正常运行时
`/health` 返回 ready `200`；runtime fatal 一经发现就撤销 ready 并开始有界
停服，在退出窗口不得继续返回 ready。preflight 或模型加载失败时记录稳定错误码
并退出，不启动一个长期返回 `503` 的后台状态服务。容器进程退出就是启动失败；
Docker `HEALTHCHECK` 只观察 `/health` 是否 ready。

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

`BOTIFIED_TTS_API_KEY` 只允许不加引号的 `[A-Za-z0-9._~-]+`。服务 `Settings`、
Skill helper 和 companion 使用同一校验规则。

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
                  |             |
                  +--cancel-----+----------> CANCELLED

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

`DRAINING` 在生成完成前继续并发接收 `cancel`，但不再接受其他客户端消息。收到
`cancel` 后立即关闭当前 `SpeechService` stream，使取消到达 Nano child，并返回
`done(cancelled=true)`；生成先完成时停止并回收接收任务，再返回正常 `done`。

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
child 释放只在同一份真实 GPU integration 中验证一次，不再复制等价测试。idle
child fatal 只有在 Nano 提供稳定 owner 接口时才做一次真实验证；不得通过读取
或修改私有进程拓扑实现。

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
- 模型下载源是唯一公开选择，必须显式设置
  `BOTIFIED_TTS_MODEL_SOURCE=modelscope|huggingface`，没有默认值，不根据地区
  或 model ID 猜测，也不在下载失败后切换来源。
- 应用内部固定且只支持以下两份 spec：

  | source | model ID | immutable revision |
  |---|---|---|
  | `modelscope` | `OpenBMB/VoxCPM2` | `2e7c0dfff6646cef46c8bf106460a3dbce23a591` |
  | `huggingface` | `openbmb/VoxCPM2` | `bffb3df5a29440629464e5e839f4d214c8714c3d` |

- 不再公开 `BOTIFIED_TTS_MODEL` 或 `BOTIFIED_TTS_MODEL_REVISION`。升级模型必须
  修改内部 spec、运行现有测试并随新镜像版本发布，不能由运行用户拼接任意组合。
- Hugging Face 使用现有固定 `huggingface-hub`；ModelScope 只增加轻量固定依赖
  `modelscope-hub==0.1.8`，不引入完整 ModelScope 平台。CUDA preflight 必须早于
  两个 SDK 的 import 和下载。
- 选择 source 后只计算一次：

  ```python
  cache_dir = settings.data_dir / "model-cache" / settings.model_source
  ```

- Hugging Face 路径只使用现代 SDK：

  ```python
  from huggingface_hub import snapshot_download

  local_path = snapshot_download(
      repo_id=spec.repo_id,
      revision=spec.revision,
      cache_dir=cache_dir,
  )
  ```

- ModelScope 路径只使用现代 `modelscope-hub` API，不使用顶层或兼容层
  `snapshot_download`：

  ```python
  from modelscope_hub import HubApi

  api = HubApi()
  local_path = api.download_repo(
      repo_id=spec.repo_id,
      repo_type="model",
      revision=spec.revision,
      cache_dir=cache_dir,
  )
  ```

- 固定 spec 只包含 repo ID 和 revision。cache 只由
  `settings.data_dir / "model-cache" / settings.model_source` 计算，两个 SDK
  都传入该值，再把本地 snapshot path 传给 Nano；生产环境解析为
  `/data/model-cache/<source>`，上述开发命令解析为
  `$PWD/.data/model-cache/<source>`。未选 SDK 不 import，失败直接返回
  `model_load_failed`。
- 运行时不得 `pip install -U`。
- 不建设逐文件 hash、processor fingerprint 或 release attestation 平台。
- 不在运行时重复校验模型仓库逐文件 hash，也不对两个内容一致的来源重复运行
  完整 GPU 路径。

## 13. 三条运行路径与Botified集成说明

### 13.1 支持环境

首版只支持：

- Linux x86_64；
- CUDA 可见且所选 device 有效的 NVIDIA GPU；
- CUDA 12.x 兼容驱动；
- Docker 和 NVIDIA Container Toolkit；
- 单个可见 GPU。

不支持 CPU fallback、Apple Silicon、Windows 或 ROCm。

当前 verified baseline 为 RTX 4090 24,564 MiB、compute capability 8.9、driver
575.64.05（driver CUDA capability 12.9）。隔离运行环境为 Python 3.12.13、
PyTorch/torchaudio 2.9.0+cu126、Triton 3.5.0、FlashAttention 2.8.3，以及第
12.3 节固定的 CUDA 12.6.3 runtime、uv 0.10.8、FFmpeg、
NumPy/Numba/llvmlite 和两份模型 spec。

该环境 warm generation 的 TTFB 为 0.1408 秒、生成阶段 RTF 为 0.1130；
`wait_for_ready()` 约 17.89 秒后首次 generation 仍有约 12.25 秒 compile，因此
ready 前必须执行真实生成 warmup。`gpu_memory_utilization=0.8` 时观测峰值显存
18,849 MiB。以上只证明当前 4090 配置，不代表最低显存或最低 compute
capability。其他 CUDA GPU 可以启动尝试但标记为未验证；应用不根据未经实测的
硬件阈值拒绝设备。

### 13.2 CUDA fail-fast

`docker run --gpus` 负责在 NVIDIA Container Toolkit 或指定宿主 GPU 不可用时
直接失败。容器只暴露用户选择的一张宿主 GPU，因此应用固定使用容器内 device
0。应用 entrypoint 在 import 模型下载 SDK、初始化 Nano 和下载 VoxCPM2 权重
前检查：

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

Power user 构建镜像不要求构建机有 GPU；GPU 事实只在容器实际启动时判断。不再
维护第二套 host preflight 脚本。

### 13.3 三条使用路径

普通 TTS 服务消费者不需要 checkout 仓库，只使用公开固定版本镜像、私有
env-file 和唯一生产启动命令：

```bash
docker run -d \
  --name botified-tts \
  --restart on-failure:3 \
  --gpus '"device=0"' \
  --env-file ./botified-tts.env \
  -p 8000:8000 \
  -v botified-tts-data:/data \
  ghcr.io/lzjever/botified-tts:v0.1.0
```

宿主 GPU index 和发布端口只通过 `--gpus` 与 `-p` 选择，不进入应用 env。
`botified-tts-data` 持久保存：

```text
/data/voices
/data/model-cache/modelscope
/data/model-cache/huggingface
```

容器重建不重复下载模型或丢失注册音色。镜像包含 reference WAV/FLAC/MP3
解码所需的 FFmpeg，并保留 Docker `HEALTHCHECK`；ready 表示 CUDA、模型下载、
Nano 加载和 warmup 已完成。容器进程退出表示启动失败，`HEALTHCHECK` 只检查
ready；运行后只用以下命令观察状态：

```bash
docker inspect --format '{{.State.Health.Status}}' botified-tts
```

结果必须为 `healthy`。未达到 `healthy` 或容器退出时只查看：

```bash
docker logs botified-tts
```

部署验证不额外发起合成请求。不增加容器内 worker restart。

Botified integrator 必须 checkout 当前仓库，才能让 Botified
`skills.explicit` 指向 Skill，并用独立 nested uv 环境运行 companion。该路径
不修改 Botified 仓库。

开发者 checkout 当前仓库后直接运行：

```bash
command -v ffmpeg
uv sync --locked
uv run pytest -q
# 可选：直接运行源码
BOTIFIED_TTS_DATA_DIR="$PWD/.data" uv run --env-file ./botified-tts.env botified-tts
```

完整本地测试唯一额外系统依赖是 `ffmpeg`；找不到时在 `uv sync` 和测试前直接
失败，不提供安装脚本、skip 或备用解码路径。

开发时 `BOTIFIED_TTS_HOST`、`BOTIFIED_TTS_PORT`、`BOTIFIED_TTS_GPU_DEVICE` 和
`BOTIFIED_TTS_DATA_DIR` 都是同一个 `Settings` 的可选 override；生产镜像固定
这些值，普通用户的 env-file 不展示它们。这不是生产部署方式。

Power user 使用根目录 Dockerfile：

```bash
docker build --platform linux/amd64 -t botified-tts:local .
```

构建完成后复用上面的 env-file、volume 和同一个 `docker run`，只替换 image
名称。不保留 Compose、部署脚本、systemd、Podman、裸机 pip 或 Kubernetes
安装器。

### 13.4 配置

普通用户的 `botified-tts.env` 权限必须为 `0600`，只包含两个必填值和一个可选
值：

```text
BOTIFIED_TTS_API_KEY=replace_with_random_hex
BOTIFIED_TTS_MODEL_SOURCE=modelscope
BOTIFIED_TTS_LOG_LEVEL=INFO
```

`BOTIFIED_TTS_LOG_LEVEL` 可省略；`BOTIFIED_TTS_MODEL_SOURCE` 也可以显式写为
`huggingface`。同一份 `botified-tts.env` 原样用于 Docker、Skill helper 和
Botified companion，不再创建单独的 key file。helper 和 companion 只通过
`--env-file ./botified-tts.env` 接收它，并安全解析恰好一条
`BOTIFIED_TTS_API_KEY=`。值按第一个 `=` 后的字面内容读取，必须是不加引号的
`[A-Za-z0-9._~-]+`；`Settings`、helper 和 companion 采用相同 grammar，不处理
quote 或 interpolation，不把文件作为 shell 脚本 source。helper 的 URL 只读取
`BOTIFIED_TTS_URL`；companion 的 URL 只接受 `--tts-url`。URL 不写入该文件。

容器镜像固定：

```text
BOTIFIED_TTS_HOST=0.0.0.0
BOTIFIED_TTS_PORT=8000
BOTIFIED_TTS_GPU_DEVICE=0
BOTIFIED_TTS_DATA_DIR=/data
```

模型 ID 和 revision 不属于运行配置。Nano 内部调优值保留为代码中的已验证
默认值，只有出现真实业务需求后才开放配置。

### 13.5 `v0.1.0` 发布

根 package 和 companion 的 `project.version` 都固定为 `0.1.0`；companion 跟随
根 package 与仓库 tag，但不独立发布。首版发布只执行以下顺序：

1. 在最终实现 commit 创建本地 Git tag `v0.1.0`。
2. 同一台可信、磁盘充足且有 CUDA 的 host 从 `v0.1.0` clean checkout，使用根
   Dockerfile 构建 Linux x86_64 image，并直接标记最终 local tag
   `ghcr.io/lzjever/botified-tts:v0.1.0`。
3. 在同一台 host 使用第 13.3 节唯一 `docker run` 启动这个 local image，并只
   通过 Docker health 确认 `healthy`；失败时查看 `docker logs`。
4. 健康后先推送 Git tag `v0.1.0`。
5. 再推送同一个已经验证的 image，并将 GHCR package 设为 public。
6. 创建一条简短 GitHub Release；正文只指向该固定 image，不附带 asset、
   changelog 或报告。

固定版本 tag 一经发布不覆盖。首版不增加 GitHub workflow、publish script、
`latest` tag、attestation 或自动化发布平台。VoxCPM2 权重不进入镜像。

中国镜像 registry 同步不在当前范围；只有出现真实拉取需求后，才把同一个固定
版本 image 同步到一个明确 registry，不新增第二套构建。

## 14. Botified 与 Agent Skill

### 14.1 Botified 集成边界

现有 Botified 是只读的外部协议和运行时依赖，负责提供 `stream_text` 事件和托管
managed task。本仓库不修改 Botified 或 Botified Gateway。

本仓库内的 `companions/botified/` 负责：

- 读取现有 Botified `stream_text`，依次发送 WebSocket `append`；
- 回答结束时发送 `finish`，同时继续读取事件；
- 用户打断或新回答替换旧回答时，立即发送 `cancel` 并停止本地播放；
- 把 PCM 交给宿主机 `aplay`；
- 使用独立 `pyproject.toml` 和 `uv.lock`，运行时只依赖轻量 WebSocket client。
- 只用 `--env-file ./botified-tts.env` 读取与服务部署相同格式的 API key，只
  用 `--tts-url` 接收服务 URL。
- 通过进程级 CLI 接收 immutable start options：`--voice-id` 与 `--design`
  二选一，另支持 `--mode` 和 `--style`；每个初始或 replacement session 都
  复用同一份 canonical `start`。

companion 是 Botified managed task 启动的薄客户端，不是独立服务或通用 bridge。
它接收已经适合朗读的纯文本并原样传递，不解析 Markdown/SSML。启用实时朗读的
Botified 工作区应在其 `AGENTS.md` 中要求 Agent 输出适合朗读的纯文本。

WebSocket 握手收到 `error` 时必须先解码安全的 code/message，再校验 `ready`
audio。错误握手不创建 `aplay` sink；异常、stdout 和 stderr 都不包含 API key。
不得把 `invalid_api_key` 等服务错误误报为 audio 不兼容。

### 14.2 Agent Skill

仓库提供：

```text
skills/voxcpm-tts/
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

helper 和 companion 使用同一参数 `--env-file ./botified-tts.env`。两者都只把
该文件当作数据逐行读取，要求恰好一条 `BOTIFIED_TTS_API_KEY=`，拒绝缺失、重复
或格式错误的 key，不 source shell，也不执行 shell 语法。helper 只从
`BOTIFIED_TTS_URL` 读取 URL，不提供 URL CLI 参数；companion 只使用
`--tts-url`，不读取 URL 环境变量。`speak --text` 必须是已经适合朗读的纯文本，
可以包含 VoxCPM2 原生非语言标签。

Skill 用于 Agent 显式生成语音文件。它不能接管同一次 LLM 回答的逐 token
stream；实时朗读由本仓库 companion 调用 WebSocket。

Botified 只推荐一种发现方式：在其配置中让 `skills.explicit` 指向当前 checkout
内的 `skills/voxcpm-tts/SKILL.md`。不推荐复制或 symlink Skill，不创建第二份
helper。Botified 仓库始终只读。

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

## 16. 最小测试范围

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
- `MODEL_SOURCE` 缺失或非法时明确失败；两个固定 model spec 的参数正确，选中源
  失败不调用另一来源。
- CUDA preflight 失败时 Hugging Face、ModelScope 和 Nano 均未 import、下载或
  创建。
- `Settings`、helper 和 companion 共用 API key grammar；env-file parser 要求
  恰好一条 `BOTIFIED_TTS_API_KEY=`，覆盖缺失、重复、空值、引号和非法字符，
  并验证按第一个 `=` 后的字面值读取、不执行 shell 内容、不输出 secret。

Nano fork 的 focused unit tests 覆盖 `wait_for_fatal()` 的 wait-before-fatal、
fatal-before-wait、AsyncPool 任一 child fatal，以及 normal stop 不触发 fatal。
不在本项目重复模拟 Nano queue、process watcher 或 scheduler。

### 16.2 API、service 与 companion integration

使用一个最小 fake Nano adapter，只验证本项目协议：

- HTTP request 经 canonical `SpeechService` 返回合法 WAV。
- WebSocket `start -> append -> binary audio -> finish -> done`。
- 生成期间仍可接收 append。
- cancel 停止当前任务并清空队列。
- `finish` 进入 `DRAINING` 后仍可接收 cancel，取消到达 Nano stream 并只返回
  一个 `done(cancelled=true)`。
- 慢客户端触发有界失败而不是无限缓存。
- idle 状态下 fake Nano fatal 无需下一请求即可使顶层 runner 撤销 ready 并抛错；
  正常 shutdown 取消 waiter 并正常返回。

fake 不模拟 Nano scheduler、KV cache、FlashAttention 或 CUDA OOM 的内部过程。

companion 使用 fake Botified frames、fake TTS WebSocket 和 fake `aplay` 覆盖：
文本顺序、immutable start options 在 replacement session 中复用、握手错误先于
sink 创建且不泄露 key、finish 后不阻塞事件读取、barge-in cancel、旧 session
完成不影响新 session，以及所有后台任务和播放器均被回收。

### 16.3 可选真实 GPU integration

仓库只保留一个显式运行的 `tests/gpu_integration.py`。同一个 engine 生命周期
覆盖真正不同的 Nano 路径：

1. CUDA preflight、所选 source 下载、tokenizer、Nano pool create/load、
   `wait_for_ready()` 和普通合成路径的真实 warmup；
2. 用一次 Voice Design 生成结果创建并保存本次脚本复用的 clone reference
   fixture；其 `prompt_text` 只保存生成音频实际朗读的 spoken text，明确排除
   Voice Design 的 description/style control prefix，并同时作为 faithful 的
   exact transcript；
3. 使用该 fixture 完成 controllable clone + style 的 reference-role latent
   路径；
4. 使用同一 fixture 完成 faithful clone 的 prompt-role latent、exact
   transcript 和 reference latent 路径；
5. 完成两段 continuation；文本中只放一个 VoxCPM2 原生非语言 tag，第二段使用
   第一段 terminal completion 的完整 generated latents；
6. 关闭真实 outer stream 后 cancel 到达 Nano child，随后同一 pool 可以完成一条
   短生成；
7. 仅当 Nano 提供稳定 owner 接口时，最后触发一次真实 child fatal，并通过公开
   `wait_for_fatal()` 有界失败；否则删除该项，不注入私有进程拓扑。

warmup 已覆盖 ordinary；Voice Design 只生成上述一份可复用 fixture，不再增加
独立重复 case。每次真实生成顺带断言 waveform 非空、finite、每个 chunk 为
7680 samples；continuation 同时断言 completion latents 非空。

功能开发和 `v0.1.0` 首发前，使用同一脚本分别选择 `modelscope` 与
`huggingface`，各至少完成一次 create → warmup。完整路径只使用其中一个 source
运行一次，不建立双 source 参数矩阵或第二份脚本。

该脚本不启动真实 HTTP/WebSocket，不测试语言或原生 tag 矩阵，不硬断言 RTF，
不建立并发 benchmark、自动音质平台、worker restart 仿真或人工听测流程。

## 17. 仓库结构

```text
botified-tts/
├── AGENTS.md
├── .gitignore
├── .dockerignore
├── Dockerfile
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
│   └── gpu_integration.py
├── companions/botified/
│   ├── README.md
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── sidecar.py
│   └── tests/test_sidecar.py
├── skills/voxcpm-tts/
│   ├── SKILL.md
│   └── scripts/botified-tts
└── docs/engineering/
    └── botified-tts-product-development-plan.md
```

不要预先创建 repository/service/use-case/domain 多层目录。只有文件真实变大并
出现明确职责边界时才拆分。

## 18. 后续改进阶段

### 阶段 1：修正未推送的 Botified 集成

当前本地 companion 与 Skill 改动尚未发布，先就地消除两个已知用户问题：

- companion 增加进程级 immutable start options，并让 replacement session
  复用；
- 握手先处理服务 error，再创建 sink 和校验 audio；
- companion 与 Skill helper 都改为只接受同一 `--env-file`，安全解析恰好一条
  API key，删除 raw key file 参数和读取路径；
- companion README 和 Botified task preset 改用 `--env-file` 与唯一
  `--tts-url`；
- Skill 文档只保留 `skills.explicit`；
- 增加 start mapping、replacement、invalid key、不创建 sink、env-file
  missing/duplicate/malformed/shell syntax 和不泄露 secret 的 focused tests。

删除空 `start` 的唯一旧路径、错误的 audio 误报和 Skill copy/symlink 文档，
不增加 companion config 文件或第二个 client。

companion README 和 task preset 只给两个最小 start 示例：

```bash
--env-file /opt/botified-tts/botified-tts.env \
  --tts-url ws://127.0.0.1:8000/v1/speech/stream \
  --voice-id voice_01k... --mode controllable --style '自然、亲切'
--env-file /opt/botified-tts/botified-tts.env \
  --tts-url ws://127.0.0.1:8000/v1/speech/stream \
  --design '温暖自然的年轻女性声音' --style '平静'
```

TLS 部署显式传入 `wss://.../v1/speech/stream`；companion 不转换 URL。

### 阶段 2：固定双模型来源

- 配置只增加必填 `MODEL_SOURCE`，删除公开 model/revision；
- 在现有 config/engine owner 内加入只含 repo ID/revision 的两项固定 spec 和
  单一 source switch；
- 固定 `modelscope-hub==0.1.8`，cache 只由 data dir 与 source 计算；
- Hugging Face 只用 `huggingface_hub.snapshot_download`，ModelScope 只用
  `modelscope_hub.HubApi.download_repo`；
- 保证 CUDA 检测早于两个 SDK import/download；
- 单元测试 source 校验、固定参数、无 fallback 和 CUDA fail-fast。

删除 HF-only 下载路径、任意 model/revision 组合和跨源 fallback，不建立 downloader
插件层。

### 阶段 3：收敛镜像与使用文档

- 把 Dockerfile 移到仓库根，并增加只允许 Dockerfile、`.dockerignore`、
  `pyproject.toml`、`uv.lock` 和 `src/**` 进入 context 的严格 `.dockerignore`；
- 删除 Compose、`deploy.sh` 及其配置、测试和文档；
- `.gitignore` 增加 `/botified-tts.env` 和 `/.data/`；
- README 依次说明普通服务消费者、Botified integrator、开发者和 Power user；
- README 写明唯一 `docker run`、只用 `docker inspect` 判断 ready、失败看
  `docker logs`；
- README 使用 `umask 077` 和 `openssl rand -hex 32` 创建唯一
  `botified-tts.env`，token 不加引号；
- README 说明完整本地测试唯一额外系统依赖是 `ffmpeg`，并在
  `uv sync --locked`、`uv run pytest -q` 和可选源码直跑命令前先执行
  `command -v ffmpeg` fail-fast；不提供安装脚本、skip 或备用解码；
- 保留 Docker `HEALTHCHECK`、`/data` volume 和应用 CUDA fail-fast。

Docker 构建只需完成根 Dockerfile build；不为删除的部署脚本建立替代测试。

### 阶段 4：真实 GPU integration 与 `v0.1.0`

- 将现有 GPU 脚本收敛为第 16.3 节的一份 opt-in integration；
- 两个 source 各完成一次 create → warmup，完整 Nano 路径只运行一次；
- 删除重复 HTTP/WS、语言/tag、RTF 阈值和私有 child 拓扑路径；
- 根 package 与 companion 的 `project.version` 都固定为 `0.1.0`，companion
  跟随根版本和仓库 tag，不独立发布；
- 严格按第 13.5 节顺序，在同一可信 CUDA host 从 tagged clean checkout 构建和
  验证，先推送 Git tag，再推送同一个已验证 image，最后创建只指向该 image 的
  GitHub Release。

不增加 workflow、publish script、`latest` tag、release asset、changelog、
发布报告或自动化发布平台。

## 19. 风险与处理

| 风险 | 最小处理 |
|---|---|
| Nano 上游差异 | 第 12.2 节固定五项薄 fork 改动 |
| 分段连续性 | 上一完整段 continuation + 真实 completion latents |
| 资源积压 | 固定输入/队列上限、send timeout、cancel |
| 环境不兼容 | 支持表、容器内 CUDA fail-fast、固定依赖 |
| 下载源不可用 | 显式失败并提示所选 source，不跨源 fallback |

## 20. 产品完成状态

产品完成时满足以下当前事实：

- companion 的 immutable start options、replacement、finish 后 barge-in 和
  握手错误均按第 14 节工作，且不泄露 API key；
- ModelScope 与 Hugging Face 都使用内部固定 spec，source 必填、无默认、无
  fallback，CUDA 失败时不 import SDK、不下载模型、不创建 Nano child；
- HTTP WAV、WebSocket 双向流、Voice Design、两种 clone、style、原生标签、
  VoiceStore、分段、continuation 和 cancel 保持现有 canonical API；
- 单元、API/service、companion focused tests 和一份 opt-in GPU integration
  覆盖第 16 节的唯一行为边界；
- 普通服务消费者只需私有 env-file 和一个固定版本 image 的 `docker run`；
  Power user 从根 Dockerfile 构建后复用同一命令，开发者使用文档中的 sync、
  test 和可选源码直跑命令；
- Botified integrator 通过 checkout 内唯一 `skills.explicit` 和 companion
  完成真实 token delta、连续播放与中断，Botified 仓库保持只读；
- 根 package 与 companion 的 `project.version` 都是 `0.1.0`，并跟随 Git tag
  `v0.1.0`；同一 host 验证 image 为 `healthy` 后先推送 Git tag、再推送该
  image，GitHub Release 只指向 image，companion 不独立发布，模型权重不在
  image 内；
- 仓库不包含 Compose、部署或发布脚本、自动发布 workflow、`latest` tag、
  跨源 fallback、重复 Skill 或第二套服务实现。
