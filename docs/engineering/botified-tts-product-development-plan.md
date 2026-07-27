# Botified TTS 产品与工程开发计划

> 状态：技术分析完成，待 Architecture Review
>
> 目标仓库：`botified-tts`
>
> 研究基线：2026-07-27
>
> 模型：OpenBMB VoxCPM2
>
> 推理内核：Nano-vLLM-VoxCPM

## 1. 文档定位

本计划用于交付 Botified 生态中的独立 TTS 服务。产品必须支持：

- VoxCPM2 音色克隆；
- Voice Design；
- 情绪、语速、音高、能量和表达风格控制；
- 笑声、叹气、犹豫等非语言声音；
- Agent token 流式输入；
- 音频流式输出；
- 跨分段语音上下文延续；
- 用户打断、取消和状态回滚；
- OpenAI Speech API 兼容子集；
- Botified Native API；
- 可供 Codex、OpenClaw 和 Botified Agent 使用的统一 Skill；
- CUDA-only 一键部署和明确的无 CUDA 失败行为。

该服务是 Botified 的独立周边服务，不属于 Botified Core、Botified Claw
Gateway 或 `botified-asr` 进程。各组件通过稳定协议独立演进：

```text
Botified / OpenClaw / other agent
        |
        | text delta / explicit synthesis request
        v
botified-tts
        |
        | Nano-vLLM-VoxCPM
        v
VoxCPM2
        |
        +-- realtime PCM -> robot/player
        |
        `-- Ogg/Opus artifact -> publish_file(audio_as_voice=true)
```

## 2. 决策摘要

### 2.1 推理引擎

首版选择
[`Nano-vLLM-VoxCPM`](https://github.com/a710128/nanovllm-voxcpm)
作为 VoxCPM2 推理内核，不选择 vLLM-Omni 作为首版核心。

原因：

1. Botified-TTS 是单一模型的专用服务，不需要 vLLM-Omni 的多模型服务复杂度。
2. Nano 已经支持 `prompt_latents`、`prompt_text` 和
   `ref_audio_latents`，与跨段 continuation 的需求直接对应。
3. Nano 内部已经持有每一步生成的 latent，只需小型扩展即可把最终 latent
   交给 Session Manager。
4. Nano 的异步生成接口适合 Botified 自己实现 WebSocket、取消、分段和
   Voice Profile。
5. 在单 GPU 和中等并发场景下，Nano 与 vLLM-Omni 的 VoxCPM2 RTF
   处于同一量级。

vLLM-Omni 只作为未来可选后端。当出现下列明确需求时再增加 adapter：

- 单 GPU 32～64 以上持续高并发；
- 大规模多租户；
- 现有运维平台已经统一采用 vLLM；
- 必须由推理服务原生暴露 OpenAI Speech API；
- 同一服务需要运行多种语音或多模态模型。

### 2.2 产品接口

Botified Native API 是完整能力契约；OpenAI API 只是兼容入口。

服务提供三层调用面：

| 调用面 | 用途 |
|---|---|
| Botified Native REST | Voice Profile、Voice Design、文件生成和管理 |
| Botified Native WebSocket | 双向流式、跨段延续、情绪更新、取消和 playback ack |
| OpenAI-compatible REST | 普通完整文本 TTS 客户端兼容 |

### 2.3 Agent 集成

Agent Skill 和实时 token bridge 是两个不同产品：

- Skill 用于 Agent 显式创建音色、选择风格、生成音频文件和语音留言。
- `botified-tts-bridge` 用于把 Agent 的实时文本 delta 转发给 TTS。

Skill 不能替代实时 bridge，因为 Skill 无法天然接收同一次 Agent 回答的每个
token delta。

## 3. 产品目标

### 3.1 必须交付

1. 一个独立部署的 CUDA-only TTS 服务。
2. VoxCPM2 reference-only 音色克隆。
3. VoxCPM2 reference + continuation 高保真音色克隆。
4. Voice Design 候选生成、试听和持久化。
5. 结构化的 emotion、pace、pitch、energy 和自定义 style instruction。
6. 官方支持的非语言标签。
7. 普通文本、Markdown 和结构化 speech parts。
8. Agent text delta 输入和 PCM 音频输出的全双工 WebSocket。
9. 服务端智能分段。
10. 生成 latent 驱动的跨段 continuation。
11. 用户打断、请求取消、未播放状态回滚。
12. OpenAI `/v1/audio/speech` 明确兼容子集。
13. 持久 Voice Profile。
14. Bearer Token 鉴权。
15. CUDA preflight、模型固定、warmup 和 readiness。
16. 一键安装和公开 release manifest。
17. 一份同时兼容 Codex、OpenClaw 和 Botified 的 Agent Skill。
18. 与 Botified LLM text preview 对接的可选 bridge。

### 3.2 首版不承诺

- 向同一个 VoxCPM2 推理请求无限追加未知文本；
- 精确到物理数值的语速、音高或停顿控制；
- 完整 SSML；
- Voice Profile 的授权证明、来源验证、滥用检测和合规审计；
- 跨进程、跨版本复用未经 fingerprint 校验的 voice latents；
- CPU fallback；
- 移动端或 Apple Silicon 推理；
- WebRTC、电话网关或实时通话平台；
- 多租户账号、组织、计费和配额系统；
- LoRA 在线训练；
- 在 Gateway 内实现 TTS 或渠道音频编码逻辑；
- 对所有语言提供完全一致的情绪和音色表现；
- 对草稿 token stream 提供最终文本一致性保证。

## 4. 上游能力和采用边界

### 4.1 VoxCPM2 基础能力

VoxCPM2 是 2B 参数、48 kHz 输出、6.25 Hz latent token rate 的多语言
TTS 模型，支持 30 种语言和 9 种中文方言。官方公开能力包括：

- 普通 TTS；
- natural-language Voice Design；
- isolated reference voice cloning；
- style-controllable voice cloning；
- continuation-based high-fidelity cloning；
- 48 kHz AudioVAE V2 输出；
- 流式音频生成；
- LoRA 和全量微调。

参考：

- [VoxCPM 官方仓库](https://github.com/OpenBMB/VoxCPM)
- [VoxCPM2 使用指南](https://voxcpm.readthedocs.io/en/latest/usage_guide.html)
- [VoxCPM2 技术报告](https://arxiv.org/abs/2606.06928)

### 4.2 三条独立控制通道

Botified-TTS 必须把下列概念分开建模：

| 通道 | 用户语义 | VoxCPM2 条件 |
|---|---|---|
| Voice Identity | 谁在说话 | isolated reference audio 或 LoRA |
| Style | 怎么说 | target text 前的自然语言 instruction |
| Continuity | 如何接着上一段说 | prompt audio latent + exact prompt text |

这三个通道不能被压缩成一个 OpenAI `voice` 字符串。

### 4.3 生成模式

服务公开下列 `voice.mode`：

| 模式 | 输入 | 优先目标 | style |
|---|---|---|---|
| `design` | voice description，无 reference | 创建新声音 | 必需或推荐 |
| `expressive` | isolated reference | 保持音色并改变表达 | 支持 |
| `faithful` | reference + paired continuation | 最大相似度和原始表达还原 | 不支持 |
| `auto` | 根据 Voice Profile 和请求解析 | 自动选择 | 条件支持 |

官方文档说明：Hi-Fi cloning 启用 paired prompt transcript 时，control
instruction 会被忽略。官方 CLI 也禁止 `--control` 与 `--prompt-text`
组合。

因此服务必须拒绝下列请求，而不是静默忽略：

```text
voice.mode=faithful + style
```

错误：

```json
{
  "error": {
    "type": "invalid_request_error",
    "code": "style_not_supported_in_faithful_mode",
    "param": "style",
    "message": "faithful voice mode cannot be combined with style control"
  }
}
```

### 4.4 Reference audio

参考音频产品约束：

- 推荐时长：5～30 秒；
- 单声道或可安全 downmix；
- 输入可接受 WAV、FLAC、MP3 和服务明确列出的格式；
- 清晰、少混响、少背景噪声；
- reference-only 不要求 transcript；
- faithful cloning 要求精确 transcript；
- denoise 默认关闭；
- denoise 只用于 noisy reference 的显式修复；
- 不把 denoise 当生成音频后处理；
- 注册时进行静音、削波、时长、可解码性和声道检查。

### 4.5 Voice Design

Voice Design description 推荐包含：

- 性别或角色；
- 年龄；
- 音高；
- 声线质感；
- 情绪；
- 节奏；
- 使用场景。

Voice Design 每次生成可能有变化。持久 Voice Profile 不能只保存
description 并在每次请求重新 design。

Canonical 工作流：

```text
description
    |
    v
1～3 个 candidate audio
    |
    v
用户或调用方选择 candidate
    |
    v
candidate audio -> reference latent
    |
    v
持久 Voice Profile
```

未 materialize 的 candidate 是临时资源，默认 24 小时后删除。

### 4.6 Style control

VoxCPM2 的 style 是自然语言文本条件，不是独立数值控制头。

Native API 接受结构化 style：

```json
{
  "preset": "empathetic",
  "emotion": "concerned",
  "intensity": "medium",
  "pace": "slow",
  "pitch": "low",
  "energy": "soft",
  "delivery": "like comforting a nervous friend",
  "instruction": "末尾语气要坚定一些"
}
```

服务内部 Style Compiler 按固定顺序生成一个 control instruction：

```text
emotion
  -> intensity
  -> pace
  -> pitch
  -> energy
  -> delivery
  -> custom instruction
```

编译结果示例：

```text
(语气关切，情绪强度中等，语速稍慢，音高略低，声音轻柔，
像在安慰一个紧张的朋友，末尾语气要坚定一些)
```

调用方不需要自己拼接圆括号。服务必须移除 control instruction 内部的
半角和全角圆括号，避免破坏模型输入约定。

结构化字段只是语义意图，不承诺：

- `pace=slow` 等于某个精确倍速；
- `pitch=low` 等于某个 Hz；
- `intensity=high` 在不同语言和音色上具有相同物理强度。

### 4.7 非语言标签

首版只允许官方推荐标签，服务用稳定事件名映射到模型标签：

| Native event | VoxCPM2 tag |
|---|---|
| `laughing` | `[laughing]` |
| `sigh` | `[sigh]` |
| `uhm` | `[Uhm]` |
| `shh` | `[Shh]` |
| `question_ah` | `[Question-ah]` |
| `question_ei` | `[Question-ei]` |
| `question_en` | `[Question-en]` |
| `question_oh` | `[Question-oh]` |
| `surprise_wa` | `[Surprise-wa]` |
| `surprise_yo` | `[Surprise-yo]` |
| `dissatisfaction_hnn` | `[Dissatisfaction-hnn]` |

未知事件返回 `400 unsupported_vocalization`。不自动猜测近义标签，不把
`[Laughter]` 等非官方变体静默改写。

Agent 和服务都不默认增加非语言声音。只有下列情况允许：

- 用户明确要求；
- 请求显式提交 speech part；
- 部署启用明确的 `auto_expressiveness` 策略。

即使启用自动策略，也必须限制每段数量，避免一条句子堆叠多个标签。

### 4.8 Pronunciation

首版 Native API 预留 pronunciation parts：

```json
{"type":"phoneme","alphabet":"pinyin","value":"{ni3}{hao3}"}
```

```json
{"type":"phoneme","alphabet":"cmudict","value":"{HH AH0 L OW1}"}
```

实现约束：

- phoneme span 内不执行 text normalization；
- 分段器不能从 phoneme span 中间切分；
- phoneme 语法必须严格校验；
- 普通文本仍是默认和推荐路径；
- 首版可以在核心流式能力完成后再开放 pronunciation parts，但 schema
  必须避免与未来字段冲突。

## 5. 总体架构

```text
                         +-----------------------------+
                         | Botified-TTS API             |
                         | REST / WebSocket / OpenAI    |
                         +--------------+--------------+
                                        |
              +-------------------------+-------------------------+
              |                                                   |
              v                                                   v
   +----------------------+                          +----------------------+
   | Voice Profile Store  |                          | Session Manager      |
   | SQLite + private FS  |                          | state/backpressure   |
   +----------+-----------+                          +----------+-----------+
              |                                                 |
              v                                                 v
   +----------------------+                          +----------------------+
   | Audio Preprocessor   |                          | Text Pipeline        |
   | decode/validate/VAE  |                          | markdown/TN/style    |
   +----------+-----------+                          +----------+-----------+
              |                                                 |
              +----------------------+--------------------------+
                                     |
                                     v
                          +----------------------+
                          | Segment Scheduler    |
                          | per-session serial   |
                          | cross-session batch  |
                          +----------+-----------+
                                     |
                                     v
                          +----------------------+
                          | Nano VoxCPM Adapter  |
                          | pinned small fork    |
                          +----------+-----------+
                                     |
                       +-------------+-------------+
                       |                           |
                       v                           v
             +------------------+        +----------------------+
             | Audio Aggregator |        | Continuation Manager |
             | PCM / codec      |        | generated latents    |
             +------------------+        +----------------------+
```

### 5.1 单一职责

| 组件 | 职责 |
|---|---|
| API | 鉴权、协议验证、错误 envelope、连接生命周期 |
| Voice Store | Voice Profile、candidate、artifact 和 fingerprint |
| Audio Preprocessor | 解码、downmix、resample、质量检查和 role-aware encode |
| Text Pipeline | Markdown、normalization、speech parts 和 style compiler |
| Segmenter | 增量文本缓冲和语义分段 |
| Session Manager | WebSocket 状态、取消、ack、回滚和资源限制 |
| Scheduler | 同 session 串行、跨 session 并发 |
| Nano Adapter | 唯一推理后端接口 |
| Continuation Manager | generated latents、完整 segment deque 和状态版本 |
| Audio Aggregator | 100～200 ms 网络 chunk、PCM 和文件编码 |

### 5.2 KISS

- 一个 Python 产品服务；
- 一个 SQLite 数据库；
- 一个私有数据目录；
- 每张 GPU 一个 Nano server worker；
- 不引入 Redis、Kafka、Celery、外部对象存储或服务发现；
- 不在首版建立通用模型插件平台；
- 不把 Voice Profile、session 和 generation job 强行塞进同一 DTO；
- Native API、OpenAI adapter 和 Skill 共用同一 canonical generation
  processor。

## 6. Voice Profile

### 6.1 资源模型

```json
{
  "id": "voice_01J...",
  "name": "robot-assistant",
  "version": 1,
  "source": {
    "type": "reference",
    "reference_duration_ms": 8200,
    "transcript_status": "verified"
  },
  "capabilities": {
    "expressive": true,
    "faithful": true,
    "lora": false
  },
  "default_style": {
    "preset": "warm"
  },
  "model_fingerprint": "sha256:...",
  "status": "ready",
  "created_at": "2026-07-27T00:00:00Z"
}
```

### 6.2 内部数据

Voice Profile 内部可包含：

- 原始 reference artifact；
- 标准化后的 16 kHz mono reference；
- isolated reference latents；
- continuation prompt latents；
- verified prompt transcript；
- Voice Design description；
- Voice Design candidate seed；
- optional LoRA adapter name；
- model fingerprint；
- preprocessing fingerprint；
- retention policy。

latent 只供服务内部使用，公共 API 不返回 float32 latent bytes。

### 6.3 Role-aware latent encoding

VoxCPM2 官方实现：

- isolated reference 使用 right padding；
- continuation prompt 使用 left padding。

当前 Nano `encode_latents()` 对所有音频采用 left padding。Botified fork
必须改为：

```python
encode_latents(wav, role="reference")
encode_latents(wav, role="continuation")
```

同一 reference file 在 faithful profile 中需要分别编码两个角色，不能假设
同一份 latent 在两个 pathway 中完全等价。

### 6.4 Voice fingerprint

`model_fingerprint` 至少覆盖：

- VoxCPM2 model revision；
- `config.json` hash；
- `model.safetensors` hash；
- `audiovae.pth` hash；
- tokenizer files hash；
- Nano fork commit；
- reference encode sample rate；
- downmix 和 resample policy；
- left/right padding policy；
- latent dtype 和 shape contract。

模型或 AudioVAE 升级后：

- fingerprint 相同：允许直接加载；
- fingerprint 不同且保留原始 reference：重新编码新 voice version；
- fingerprint 不同且未保留 reference：标记 `stale`，要求重新上传；
- 禁止静默使用不兼容 latents。

### 6.5 Voice Design candidate

创建请求：

```http
POST /v1/voice-candidates
```

```json
{
  "description": "温暖、自然的年轻女性声音，音高略低，语速舒缓",
  "sample_text": "你好，我是你的机器人助手，很高兴认识你。",
  "count": 3,
  "seed": 42
}
```

约束：

- `count` 为 1～3；
- 每个 candidate 使用独立 seed；
- sample text 应产生至少约 5 秒声音；
- candidate 不自动成为 Voice Profile；
- materialize 前允许用户试听；
- candidate 默认 24 小时过期；
- candidate audio 不用于其他用户或其他部署。

固化：

```http
POST /v1/voice-candidates/{candidate_id}/materialize
```

```json
{
  "name": "warm-assistant",
  "retain_source_audio": true
}
```

## 7. Text Pipeline

### 7.1 输入格式

Native API 支持：

```text
plain_text
markdown
parts
```

默认：

- Agent bridge：`markdown`
- 普通 SDK：`plain_text`
- rich generation：`parts`

### 7.2 Speech parts

```json
{
  "format": "parts",
  "parts": [
    {"type":"text","text":"我想了想，"},
    {"type":"vocalization","name":"uhm"},
    {"type":"text","text":"我们还是现在出发吧。"},
    {"type":"break","strength":"medium"},
    {"type":"text","text":"不用担心，我会陪着你。"}
  ]
}
```

`break.strength` 只接受：

```text
short
medium
long
```

实现使用标点和 segment boundary 表达，不对外承诺精确毫秒。

### 7.3 Markdown policy

增量 Markdown 处理默认规则：

- 标题标记不朗读；
- emphasis 标记不朗读；
- link 只朗读 label；
- URL 默认不朗读完整协议和 query；
- inline code 默认按普通短文本处理；
- fenced code 默认跳过，并发送 `code_block_skipped` warning；
- list item 之间加入自然停顿；
- block quote 朗读正文；
- image 只朗读 alt text；
- HTML 默认移除 tag，保留可见文本；
- 未闭合 Markdown span 在超时前保留有限缓冲；
- 缓冲达到上限时按安全文本 fallback，不无限等待。

### 7.4 Text normalization

VoxCPM 官方使用 WeText 对中文和英文进行文本正规化。Botified-TTS
前处理层复用相同能力，不在 Nano engine 内复制。

公开选项：

```text
normalization=auto
normalization=on
normalization=off
```

默认 `auto`：

- 对官方 normalizer 明确支持的中文和英文执行；
- phoneme span 不执行；
- 模型名、代码、URL 和已标记 verbatim span 不执行；
- 其他语言保留原文；
- normalization 在完整 segment commit 后执行，不逐 token 调用。

### 7.5 两种文本表示

每个 segment 保留：

- `spoken_text`：用户预期听到的正文；
- `model_text`：包含 style instruction、非语言标签和模型控制文本。

两者只保存在 session 内存和必要的内部 continuation state。默认不写日志、
数据库或 metrics label。

## 8. Native HTTP API

### 8.1 鉴权

除 `/health/live` 外，全部产品端点要求：

```http
Authorization: Bearer <BOTIFIED_TTS_API_KEY>
```

规则：

- 单部署级 API Key；
- constant-time 比较；
- API Key 只从环境变量读取；
- YAML 不允许明文 key；
- 日志和错误不回显 key；
- `/health/ready` 也要求鉴权；
- 发行运行时不公开 interactive Swagger UI；
- OpenAPI JSON 在 release 构建时离线生成。

### 8.2 Health

```http
GET /health/live
GET /health/ready
```

`live` 只证明事件循环可响应。

`ready` 必须证明：

- CUDA preflight 通过；
- 指定 GPU 可用；
- 模型文件 hash 通过；
- Nano worker ready；
- reference encoder ready；
- SQLite 和私有目录可用；
- 固定 warmup generation 成功；
- scheduler 已启动。

### 8.3 Capabilities

```http
GET /v1/capabilities
```

返回：

- model alias 和 fingerprint；
- 支持语言；
- 支持 voice modes；
- 支持 vocalization events；
- 支持输入格式；
- 支持输出编码；
- sample rate；
- max reference duration；
- max session limits；
- OpenAI compatibility version；
- `faithful + style` 不兼容说明；
- realtime protocol version。

不返回：

- 本地路径；
- CUDA device UUID；
- API key；
- voice 列表；
- 完整依赖版本；
- 原始模型下载 URL。

### 8.4 Voice endpoints

```text
POST   /v1/voice-candidates
GET    /v1/voice-candidates/{id}
DELETE /v1/voice-candidates/{id}
POST   /v1/voice-candidates/{id}/materialize

POST   /v1/voices
GET    /v1/voices
GET    /v1/voices/{id}
DELETE /v1/voices/{id}
```

`POST /v1/voices` 使用 `multipart/form-data` 注册 reference voice：

- `metadata`：JSON；
- `file`：唯一 reference audio；
- optional `transcript`；
- optional `denoise`；
- optional `retain_source_audio`。

禁止：

- 多个 file part；
- URL 形式任意远程下载；
- 任意本地路径；
- 未知 metadata 字段；
- 重复 scalar 字段；
- 不受限的 multipart body；
- 超过部署上限的 reference；
- 空白或纯静音 reference。

### 8.5 Native generation

```http
POST /v1/speech/generations
```

请求：

```json
{
  "voice": {
    "id": "voice_01J...",
    "mode": "expressive"
  },
  "style": {
    "preset": "empathetic",
    "emotion": "concerned",
    "pace": "slow",
    "energy": "soft"
  },
  "input": {
    "format": "parts",
    "parts": [
      {"type":"text","text":"我想了想，"},
      {"type":"vocalization","name":"uhm"},
      {"type":"text","text":"我们还是现在出发吧。"}
    ]
  },
  "output": {
    "encoding": "ogg_opus",
    "delivery": "artifact"
  },
  "generation": {
    "seed": 42,
    "adherence": "balanced"
  }
}
```

`output.delivery`：

| 值 | 语义 |
|---|---|
| `stream` | HTTP audio byte stream |
| `artifact` | 完整生成后返回私有 generation artifact |

实时交互推荐 WebSocket，不推荐长期 HTTP chunked stream。

### 8.6 Generation 参数

Agent-facing 参数：

- `voice.mode`
- `style`
- `seed`
- `adherence`
- `output`

首版不直接向 Agent 暴露：

- `temperature`
- `cfg_value`
- `inference_timesteps`
- `max_generate_length`
- KV cache 参数
- VAE chunk 参数

`adherence` 到 `cfg_value` 的映射由真实 GPU A/B 测试确定：

```text
natural
balanced
strict
```

映射属于 release 配置和 processor fingerprint，不能在 patch release
静默改变。

Nano 当前 `inference_timesteps` 是进程级配置。首版固定为 `10`，不伪装成
逐请求参数。

## 9. Realtime WebSocket API

### 9.1 Session 创建

```http
POST /v1/speech/sessions
```

```json
{
  "voice": {
    "id": "voice_01J...",
    "mode": "expressive"
  },
  "style": {
    "preset": "warm"
  },
  "input_format": "markdown",
  "segmentation": {
    "mode": "semantic",
    "profile": "balanced"
  },
  "continuity": {
    "enabled": true,
    "on_style_change": "reset"
  },
  "output": {
    "encoding": "pcm_s16le",
    "sample_rate": 48000,
    "chunk_ms": 160
  }
}
```

返回 session ID、WebSocket URL、过期时间和 protocol version。session
是短期运行资源，不进入 durable job 系统。

### 9.2 Client JSON frames

```text
session.configure
input.text.append
input.vocalization.append
input.text.commit
style.update
input.done
playback.ack
response.cancel
session.close
```

所有 client frame：

- 包含严格递增的 `client_seq`；
- 单个 JSON frame 有明确字节上限；
- 未知 `type` 返回稳定协议错误；
- 未知字段 fail closed；
- 同一个 session 只允许一个 active response；
- WebSocket 顺序是 canonical 顺序，不支持乱序补帧；
- 重连不续传旧音频流，建立新 session。

示例：

```json
{
  "type": "input.text.append",
  "client_seq": 1,
  "text": "嗯，我想了一下，"
}
```

```json
{
  "type": "input.vocalization.append",
  "client_seq": 2,
  "name": "sigh"
}
```

```json
{
  "type": "style.update",
  "client_seq": 3,
  "style": {
    "emotion": "excited",
    "pace": "fast"
  }
}
```

```json
{
  "type": "input.done",
  "client_seq": 4
}
```

### 9.3 Server JSON frames

```text
session.ready
input.accepted
segment.committed
segment.started
audio.started
segment.completed
response.completed
response.cancelled
warning
error
```

关键事件字段：

- session ID；
- response ID；
- segment sequence；
- audio sequence；
- style revision；
- continuity revision；
- sample rate；
- audio encoding；
- stop reason；
- stable error/warning code。

### 9.4 Binary audio frame

音频不使用 base64 JSON。Realtime v1 使用固定 binary header：

| Offset | Size | 字段 |
|---:|---:|---|
| 0 | 4 | magic `BTSA` |
| 4 | 1 | version `1` |
| 5 | 1 | flags |
| 6 | 2 | header length，network byte order |
| 8 | 8 | audio chunk sequence，network byte order |
| 16 | 4 | segment sequence，network byte order |
| 20 | 8 | sample offset，network byte order |
| 28 | N | PCM payload |

PCM payload：

- signed 16-bit little-endian；
- mono；
- 48 kHz；
- 默认 160 ms；
- 配置允许范围 100～200 ms；
- 默认约每秒 6.25 个网络 chunk；
- 任何时候不超过每秒 10 个网络 chunk。

内部 Nano chunk 可以更细，Audio Aggregator 负责合并。

### 9.5 Backpressure

必须限制：

- 单 append 字节数；
- 未 commit text 总字节数；
- pending segment 数；
- session 总时长；
- session idle 时间；
- 待发送音频时长；
- 未确认播放音频时长；
- 单 session continuation context；
- 全局 active sessions；
- 每 GPU active sequences。

慢客户端不能导致无界队列。达到上限后：

1. 暂停提交新 segment；
2. 保留短暂宽限；
3. 仍无法恢复时取消 active generation；
4. 发送或记录 `client_too_slow`；
5. 关闭 session；
6. 回收 continuation 和 audio buffers。

Nano fork 的进程间 stream queue 也必须有界，不能只限制 WebSocket 层。

## 10. 智能分段

### 10.1 官方约束

VoxCPM2 当前支持完整文本输入、流式音频输出，不支持向同一个推理调用持续追加
token。官方建议交互场景：

1. 把到达文本切成句子；
2. 每句调用一次 streaming generation；
3. 顺序播放音频。

因此 Botified-TTS 的“双向流式”定义为：

```text
外部协议持续接收 text delta
        +
服务内部提交完整 segment
        +
模型持续生成 audio chunk
        +
接收下一段文本与播放上一段音频并行
```

不得宣称 VoxCPM2 本身实现了 unknown future text append。

### 10.2 目标

分段器平衡：

- 首音频延迟；
- 最小稳定语音时长；
- 自然语义边界；
- 上下文长度；
- style/markup/tag 完整性；
- 中英文和其他语言差异；
- Agent Markdown；
- 用户显式 commit。

### 10.3 Canonical 边界

优先级：

1. `input.text.commit`
2. `input.done`
3. 句号、问号、感叹号及等价标点
4. 达到最小时长后的逗号、分号、冒号
5. paragraph/list boundary
6. latency deadline
7. hard maximum

不得在下列位置切分：

- phoneme span 内；
- vocalization tag 内；
- 未闭合 Markdown link 内；
- 数字、小数、日期或 URL 中间；
- style control 内；
- emoji sequence 中间；
- grapheme cluster 中间。

### 10.4 时长目标

不使用“固定字符数”作为唯一规则。分段器估算语音时长：

- 理想 segment：约 1.5～6 秒；
- hard maximum：约 8～12 秒；
- 太短输入先缓存；
- `input.done` 允许提交短尾段；
- hard punctuation 可在最小稳定时长附近提前提交；
- 参数在 Phase 0 真实音频测试后固定。

官方指出短于约 1 秒的生成稳定性较弱，长文本容易出现逐渐加速、buzzing、
KV cache 增长或无法停止。分段是质量策略，不只是接口兼容层。

## 11. 跨段 Continuation

### 11.1 Nano 扩展

当前 Nano 公共 `generate()` 只 yield waveform，但 engine 的
`postprocess_seq()` 已经取得每个生成 latent。

Botified fork 增加：

```python
@dataclass
class SegmentResult:
    request_id: str
    generated_latents: bytes
    generated_patch_count: int
    seed: int | None
    stop_reason: str
```

stream 消息：

```text
audio
complete
error
cancelled
```

生成 latent 不发送给公共客户端，只交给产品进程内的 Continuation Manager。

### 11.2 完整 segment deque

模型输出没有可靠的字符级或词级 latent alignment，因此不得：

```text
直接截取最后 3 秒 latent
  +
猜测对应的 prompt_text
```

Continuation state 按完整 segment 保存：

```text
SegmentState:
  segment_seq
  spoken_text
  model_text
  generated_latents
  audio_samples
  style_revision
  playback_state
```

达到 prompt 预算时按完整 segment 淘汰最旧项。某一 segment 本身超过
continuation budget 时，下一段重置 continuation。

是否在 continuation prompt 中保留 non-verbal model tag，以及 style prefix
是否只用于首段，必须通过 Phase 0 A/B 验证。首选候选策略：

- prompt transcript 使用 `spoken_text`；
- 保留必要的官方 non-verbal tag；
- 不把 style description 当成被朗读 transcript；
- style 通过上一段生成音频自然传递。

这项策略在验证完成前不能宣称完全等同于官方 Hi-Fi prompt cache。

### 11.3 Speculative 与 committed state

为了同时满足低延迟和中断正确性，维护两份逻辑状态：

```text
committed continuation
speculative continuation
```

流程：

1. Segment N 推理完成。
2. N 立即成为 speculative continuation。
3. N+1 可在 N 播放期间开始生成。
4. 客户端完整播放 N 后发送 `playback.ack`。
5. N 被提升为 committed continuation。
6. 用户中断时取消 active 和 queued segments。
7. 丢弃未完整播放的 speculative states。
8. 回滚到最后一个 committed segment。

如果客户端不支持 playback ack：

- 服务只能以 `sent` 作为近似 watermark；
- session capability 返回 `playback_ack=false`；
- 中断后默认更保守地重置 continuation；
- 不把 sent 等价描述为 played。

### 11.4 Style transition

默认：

```text
continuity.on_style_change=reset
```

规则：

| 变化 | 行为 |
|---|---|
| style 未变化 | 保持 continuation |
| 仅无语义 metadata 变化 | 保持 continuation |
| emotion/pace/pitch/energy 变化 | 清空 continuation，保留 reference |
| expressive -> faithful | 新 response，拒绝在原 response 热切换 |
| faithful -> expressive | 新 response，清空 continuation |
| voice ID 变化 | 新 response 和新 continuation |

示例：

```text
calm -> calm        continuation
calm -> excited     reset continuation, keep voice identity
excited -> excited  continuation
```

## 12. Nano-vLLM-VoxCPM Fork

### 12.1 Fork 原则

- Fork 保持很薄；
- 产品 API 不进入 fork；
- Voice Store 不进入 fork；
- WebSocket 不进入 fork；
- 上游可接受的通用修复优先提交 upstream；
- release 固定完整 commit；
- 每次上游升级重跑真实 GPU parity suite。

### 12.2 必须修改

1. 输出 generated latents。
2. 新增 `complete/error/cancelled` stream message。
3. role-aware reference/continuation encoding。
4. 记录 generation start offset，不从混合 `feats` 猜测。
5. 有界进程间 output queue。
6. 客户端关闭 async generator 时可靠取消 request。
7. 完成或取消后释放 sequence、KV block 和 latent buffer。
8. request ID 和 session-owned metadata 透传。
9. stable stop reason。
10. 对 CUDA OOM、invalid prompt length 和 worker exit 提供稳定异常类别。

### 12.3 不放进 Fork

- Bearer auth；
- OpenAI route；
- Voice Profile；
- SQLite；
- text normalization；
- Markdown；
- style compiler；
- segmentation；
- Ogg/Opus；
- Agent Skill；
- Botified bridge。

### 12.4 上游 parity

至少验证：

- reference-only；
- continuation-only；
- reference + continuation；
- style control；
- zero-shot Voice Design；
- seed；
- runtime LoRA；
- PCM waveform duration；
- first chunk latency；
- stop condition；
- concurrent generation；
- cancellation；
- reference right padding；
- continuation left padding。

## 13. OpenAI-compatible API

### 13.1 定位

```http
POST /v1/audio/speech
```

“兼容”只表示明确子集。标准 OpenAI SDK 可在自定义 `base_url` 下完成普通
speech generation；不宣称兼容全部 OpenAI Audio API。

### 13.2 首版字段

| 字段 | 支持 |
|---|---|
| `model` | 固定 alias `voxcpm2` |
| `input` | 完整非空文本 |
| `voice` | Voice Profile ID 或唯一名称 |
| `response_format` | `pcm`、`wav`、`mp3`、`opus` |
| `speed` | 首版只接受 `1.0` |

非 `1.0` speed 返回明确错误。VoxCPM2 的 pace 是自然语言条件，不是精确
时间拉伸；Native API 使用 `style.pace`。

### 13.3 不支持

- input token streaming；
- Voice Profile 创建；
- Voice Design；
- speech parts；
- vocalization event；
- style update；
- continuation；
- playback ack；
- realtime cancel；
- arbitrary model ID；
- response 内扩展 latent。

不把大量 Botified 私有字段塞进 OpenAI endpoint。丰富能力只走 Native API。

## 14. Botified TTS Bridge

### 14.1 现有输入

Botified 已有 live-only `/v1/llm-text-preview`，frame 包括：

```text
started
text_delta
finished
aborted
error
status
```

Bridge 映射：

| Botified | Botified-TTS |
|---|---|
| `started` | create/reset response |
| `text_delta` | `input.text.append` |
| `finished` | `input.done` |
| `aborted` | `response.cancel` |
| `error` | `response.cancel` |

Bridge 必须使用 `provider_request_id`、`cycle_id`、`provider_call_index` 和
`input_ids` 区分多次 provider call，不能把不同 call 的文本拼进同一个
TTS response。

### 14.2 两种模式

```text
low_latency_draft
committed
```

`low_latency_draft`：

- 消费 preview delta；
- 低延迟；
- preview 是草稿；
- provider 后续可能调用工具、abort 或改写；
- 已播放语音无法撤回；
- abort 时取消未发送/未播放部分；
- UI 和文档必须显示 draft 语义。

`committed`：

- 从 timeline final text 生成；
- 文本正确；
- 没有 token 输入流式收益；
- 适合语音留言和正式播报。

不能静默从一种模式切换到另一种。

### 14.3 不修改 Gateway ownership

实时机器人播放由 player/robot integration 处理。

聊天平台语音留言：

```text
TTS 生成 Ogg/Opus artifact
        |
        v
Botified publish_file(audio_as_voice=true)
        |
        v
Botified Claw Gateway
        |
        v
官方 channel plugin
```

Gateway 不选择声音、不调用 TTS、不管理 Voice Profile，也不实现新的模型或
channel-specific TTS policy。

## 15. Agent Skill

### 15.1 唯一源码

```text
skills/botified-tts/
├── SKILL.md
├── agents/openai.yaml
├── scripts/botified-tts
└── references/
    ├── api.md
    └── style-guide.md
```

`SKILL.md` frontmatter：

```yaml
---
name: botified-tts
description: Use when creating or cloning a voice, generating expressive speech, or producing a voice-message audio file through Botified TTS.
---
```

不增加 Codex、OpenClaw 或 Botified 私有 frontmatter。

### 15.2 Skill 职责

Skill 指导 Agent：

- 从唯一 `client.env` 读取连接目标；
- 先检查 ready；
- 判断 reference voice、Voice Design、expressive 和 faithful；
- 创建 Voice Design candidate，并让用户选择；
- 注册 reference voice；
- 选择 emotion、pace、energy 和 vocalization；
- faithful 模式不提交 style；
- 不擅自增加笑声、叹气或犹豫；
- 生成 PCM/WAV/MP3/Ogg/Opus；
- 语音留言优先 Ogg/Opus；
- 需要渠道 voice presentation 时使用
  `publish_file(audio_as_voice=true)`；
- 不在输出中泄漏 API key、reference audio、latents 或私有路径；
- 删除 Voice Profile 时说明相关生成能力会失效。

### 15.3 Deterministic helper

```text
health
capabilities
voice-candidate-create
voice-candidate-get
voice-create-reference
voice-create-from-candidate
voice-list
voice-get
voice-delete
synthesize
preview
```

约束：

- helper 是薄 wrapper，不实现第二套 TTS pipeline；
- helper 不保存 API key；
- helper 输出 JSON；
- audio 写到显式输出路径，不把 binary 打到 Agent context；
- 长文本通过 stdin、`--text-file` 或 request JSON file 输入；
- 服务错误保留 stable error code；
- 不替 Agent 自动决定人物身份或发布渠道；
- 不自动安装系统依赖；
- 缺少依赖时 fail fast 并给出明确前置条件。

### 15.4 客户端配置

```text
${XDG_CONFIG_HOME:-$HOME/.config}/botified-tts/client.env
```

只接受：

```text
BOTIFIED_TTS_BASE_URL=http://127.0.0.1:17771
BOTIFIED_TTS_API_KEY=<secret>
```

规则沿用 `botified-asr`：

- 键白名单解析；
- 不 `source` 文件；
- mode `0600`；
- 环境变量只作为显式一次性覆盖；
- URL 和 key 成对切换；
- 不修改服务端 `service.env`；
- token 不写入 Skill、README 命令示例或 shell profile。

### 15.5 安装位置

同一个 skill tarball：

| Runtime | 默认目录 |
|---|---|
| Codex | `~/.codex/skills/botified-tts` |
| OpenClaw | `~/.agents/skills/botified-tts` |
| Botified | `~/.local/share/botified/skills/botified-tts` |

`install-tts-skill.sh --target <codex|openclaw|botified>` 必须显式指定 target，
不自动检测后静默选择。

## 16. CUDA-only 部署

### 16.1 支持范围

首版：

```text
Linux x86_64
NVIDIA CUDA
one or more supported NVIDIA GPUs
```

不支持 CPU、MPS、ROCm、XPU 和无验证的 compute capability。

### 16.2 启动顺序

必须在下载模型和创建 Nano worker 前：

1. 检查 NVIDIA container/runtime；
2. 检查 GPU visibility；
3. `torch.cuda.is_available()`；
4. 检查 device count；
5. 检查配置 device index；
6. 检查 driver/runtime/PyTorch compatibility；
7. 检查 FlashAttention 和 Triton；
8. 检查 compute capability；
9. 检查可用显存；
10. 检查模型 cache 磁盘空间。

无 CUDA：

```text
level=error code=cuda_unavailable
message="Botified TTS requires a supported NVIDIA CUDA device; CPU fallback is disabled"
```

进程非零退出。不得先下载约 5 GB 权重再失败。

### 16.3 当前审计基线

```text
OpenBMB/VoxCPM:
616d3d3e630a9c96c2853250eef91b0f39dcd5fa

a710128/nanovllm-voxcpm:
0ef61b0ba634dbf2fad9e916bc4fb696a3c0f51f

openbmb/VoxCPM2:
bffb3df5a29440629464e5e839f4d214c8714c3d
```

正式 release 固定：

- OCI digest；
- base image digest；
- CUDA runtime；
- Python；
- PyTorch；
- FlashAttention；
- Triton；
- Nano fork commit；
- VoxCPM2 HF revision；
- 全部运行时模型文件 SHA-256；
- FFmpeg 版本或外部前置条件；
- processor fingerprint。

容器启动时禁止：

- `pip install -U`；
- 解析 mutable `main`；
- 自动切换模型；
- 无 hash 下载后直接加载；
- 无 CUDA 时尝试 CPU。

### 16.4 一键安装

`install-tts.sh` 必须：

1. 检查平台和架构；
2. 检查 Docker/Podman 支持范围；
3. 检查 NVIDIA Container Toolkit；
4. 检查真实 GPU；
5. 检查 driver 和 compute capability；
6. 检查磁盘和端口；
7. 下载 release manifest；
8. 校验 manifest 和 installer artifact；
9. 按 digest 拉取 image；
10. 创建私有 config/data/cache 目录；
11. 创建 `service.env` 和 `client.env`；
12. 启动容器；
13. 等待 ready；
14. 执行固定 TTS smoke；
15. 输出服务地址和 Skill 安装提示。

安装失败不得留下“看似已安装但永远不 ready”的 active service。

## 17. 配置

### 17.1 YAML

建议：

```yaml
version: 1

service:
  host: 127.0.0.1
  port: 17771
  api_key_env: BOTIFIED_TTS_API_KEY

model:
  alias: voxcpm2
  path: openbmb/VoxCPM2
  revision: bffb3df5a29440629464e5e839f4d214c8714c3d
  devices: [0]
  inference_timesteps: 10
  max_model_len: 4096
  max_num_seqs: 16
  gpu_memory_utilization: 0.95

realtime:
  output_chunk_ms: 160
  max_sessions: 64
  max_pending_segments_per_session: 8
  max_uncommitted_text_bytes: 262144
  max_audio_buffer_secs: 5
  idle_timeout_secs: 60
  max_session_secs: 1800

segmentation:
  profile: balanced

voices:
  retain_source_audio: true
  candidate_retention_hours: 24

storage:
  data_dir: /var/lib/botified-tts
  max_bytes: 21474836480

logging:
  level: info
```

所有上限在真实 GPU capacity test 后固定。release 默认值一旦成为公开契约，
不得在 patch release 中大幅漂移。

### 17.2 Secret

```text
${XDG_CONFIG_HOME:-$HOME/.config}/botified-tts/service.env
```

只包含：

```text
BOTIFIED_TTS_API_KEY=<secret>
```

mode `0600`。YAML 不接受明文 `api_key`。

## 18. 数据边界

### 18.1 当前信任模型

本项目当前是内部项目，运行在可信内网中，输入的 reference audio、Voice
Design 描述和 Voice Profile 均视为可信数据。因此，首版不设计或实现：

- Voice Profile 的授权声明和授权证明；
- reference audio 来源核验；
- 人物身份识别或公众人物检测；
- 音色克隆滥用检测；
- 水印、内容溯源和对外合规审计；
- 面向不可信租户的 Voice Profile 访问隔离。

这些能力不进入当前 API、错误码、数据库字段、Skill 工作流或发布验收。
如果未来服务暴露到公网、接入不可信数据或转为多租户产品，需要重新完成威胁
建模，并作为独立安全阶段设计，不能默认沿用本版本的信任假设。

Bearer Token、输入大小限制、路径隔离、secret 脱敏和资源清理仍然保留。
它们属于服务运行边界和稳定性要求，不构成 Voice Profile 合规治理。

### 18.2 私有数据

默认不记录：

- reference audio 内容；
- reference transcript；
- target text；
- style instruction；
- generated audio；
- latent；
- Voice Profile name；
- Authorization；
- request body；
- Botified preview delta。

可记录：

- request/session ID；
- voice ID 的不可逆短 hash；
- voice mode；
- 输入字符/字节数；
- segment 数；
- audio duration；
- queue wait；
- TTFB；
- RTF；
- status；
- stable error code。

### 18.3 删除

删除 Voice Profile：

- 标记删除并停止新请求；
- 等待或取消正在使用该 version 的 session，按 API 语义固定；
- 删除 reference artifacts；
- 删除 derived latents；
- 删除 candidate linkage；
- 删除 optional LoRA association；
- 清理 prefix cache；
- 记录不含音色内容的 audit event；
- 返回资源不可恢复说明。

首版使用本地文件系统，删除是 best-effort 文件删除，不宣称物理介质安全擦除。

## 19. 多 GPU 和调度

### 19.1 Session serial

同一 session 的 segment 推理必须串行，因为 Segment N+1 的 continuation
依赖 Segment N 的 generated latents。

文本输入、上一段播放和下一段等待可并行，但同一 session 不同时运行两个互相
依赖的 segment generation。

### 19.2 Cross-session batching

不同 session 由 Nano continuous batching 合并。Scheduler 不实现第二套
GPU batching。

### 19.3 Sticky GPU

多 GPU 时 session 默认固定到一个 worker：

- 增加 prefix cache 命中；
- 避免 reference context 重复预填；
- 简化 request cancellation；
- 保持性能稳定。

worker 失败后：

- active segment 失败；
- session 返回 `engine_worker_lost`；
- speculative continuation 丢弃；
- committed continuation 可在新 worker 上通过 latents 重建；
- 不静默重复已经播放的音频。

## 20. 音频输出

### 20.1 Realtime

Canonical：

```text
48 kHz
mono
PCM s16le
160 ms network chunk
```

Realtime 不默认 MP3，避免编码延迟、frame buffering 和播放端差异。

### 20.2 Artifact

支持：

- WAV；
- PCM；
- MP3；
- Ogg/Opus。

语音留言推荐：

```text
audio/ogg
Ogg/Opus
48 kHz mono
```

转码在完整 artifact 路径完成，不影响 VoxCPM2 内部 48 kHz generation。

### 20.3 Gapless

同 session 的音频 timeline 使用 sample offset，客户端按 offset 播放。服务不在
segment boundary 任意插入静音。

必要的 crossfade 只能作为实验性 fallback：

- 默认关闭；
- 不替代 continuation；
- 参数进入 processor fingerprint；
- 通过听感和波形边界测试后才能开启；
- 不能掩盖重复音素或截断音素。

## 21. 错误契约

REST 使用 OpenAI 风格 envelope：

```json
{
  "error": {
    "message": "human-readable message",
    "type": "invalid_request_error",
    "param": "voice.mode",
    "code": "invalid_voice_mode"
  }
}
```

稳定错误至少包括：

```text
invalid_api_key
service_not_ready
cuda_unavailable
cuda_unsupported
insufficient_gpu_memory
model_artifact_mismatch
engine_worker_lost
engine_oom
invalid_voice
voice_not_ready
voice_fingerprint_mismatch
invalid_reference_audio
reference_too_short
reference_too_long
reference_silent
prompt_transcript_required
style_not_supported_in_faithful_mode
unsupported_vocalization
invalid_phoneme
input_too_large
session_limit_exceeded
session_expired
client_too_slow
generation_cancelled
continuation_reset
```

WebSocket error frame 使用同一 stable code，不定义第二套错误词汇。

## 22. Observability

### 22.1 Metrics

至少：

```text
tts_requests_total
tts_sessions_active
tts_segments_total
tts_generation_errors_total
tts_queue_wait_seconds
tts_text_buffer_seconds
tts_audio_ttfb_seconds
tts_rtf
tts_audio_seconds_total
tts_audio_chunks_total
tts_playback_unacked_seconds
tts_cancellations_total
tts_continuity_resets_total
tts_engine_worker_restarts_total
tts_cuda_memory_bytes
tts_voice_encode_seconds
```

metrics label 禁止使用：

- target text；
- style instruction；
- voice name；
- reference filename；
- session/user provided strings；
- API key；
- raw error message。

### 22.2 日志

结构化日志包含：

- request/session ID；
- response/segment sequence；
- engine worker；
- model fingerprint short ID；
- mode；
- style revision；
- continuity revision；
- byte/character/audio duration；
- queue/TTFB/RTF；
- status 和 stable code。

默认不记录内容。

## 23. 质量评估

### 23.1 指标

| 维度 | 评估 |
|---|---|
| 可懂度 | ASR WER/CER |
| 音色 | speaker embedding cosine similarity |
| 自然度 | MOS/UTMOS + 人工听测 |
| Style | 指令跟随人工评分/分类器 |
| Boundary | gap、click、重复音素、截断音素 |
| Realtime | text wait、TTFB、RTF、underrun |
| Non-verbal | tag 命中和自然度人工检查 |

speaker similarity 只用于质量比较，不称为身份概率。

### 23.2 A/B 矩阵

必须比较：

1. reference-only；
2. continuation-only；
3. reference + continuation；
4. ref-only + style；
5. first-segment style + generated continuation；
6. style change without reset；
7. style change with reset；
8. whole-segment continuation window 1/2/3 segments；
9. static reference every segment；
10. output chunk 100/160/200 ms；
11. different segmentation latency；
12. denoise on/off；
13. cfg/adherence 档位；
14. non-verbal tags；
15. Markdown Agent 输出；
16. 中断和 rollback。

### 23.3 测试语言

至少：

- 普通话；
- 英语；
- 中英混合；
- 粤语真实用词；
- 日语；
- 韩语；
- 一种长单词/复杂标点的欧洲语言；
- 一种 RTL 语言。

方言测试必须使用真实方言词汇，不能只给标准普通话加一个方言 control。

## 24. 测试

### 24.1 CPU-safe unit tests

不加载 CUDA：

- API schema；
- mode conflict；
- style compiler；
- Markdown incremental parser；
- segmentation；
- speech parts；
- vocalization mapping；
- phoneme validation；
- Voice Profile storage；
- fingerprint；
- auth；
- error envelope；
- WebSocket state machine；
- playback ack；
- speculative rollback；
- queue limits；
- installer manifest parsing。

### 24.2 Fake engine integration

使用确定性的 fake Nano adapter：

- 固定 waveform；
- 固定 generated latents；
- first chunk delay；
- worker OOM；
- cancellation；
- stop reason；
- disconnect；
- slow client；
- out-of-order internal event；
- worker loss。

### 24.3 Real CUDA tests

真实受支持 GPU：

- CUDA preflight；
- model load；
- warmup；
- zero-shot；
- reference clone；
- expressive style；
- faithful clone；
- Voice Design；
- generated latent continuation；
- 8/16 并发；
- cancellation；
- worker restart；
- memory ceiling；
- long-running soak；
- audio quality samples。

未通过真实 CUDA runner 的 image 不得标记 supported。

### 24.4 Missing CUDA

必须验证：

- `CUDA_VISIBLE_DEVICES=""`；
- 无 NVIDIA runtime；
- device index 不存在；
- driver 不兼容；
- FlashAttention 不可用；
- compute capability 不支持；
- 显存不足。

这些场景不得进入模型下载和推理。

## 25. 发布验收

在固定 reference hardware 上记录，而不是只声明“实时”：

- segment commit 到 engine first audio；
- Agent first delta 到 first playable audio；
- RTF；
- concurrency 1/8/16/32；
- audio chunks/sec；
- cancellation latency；
- segment gap；
- playback underrun；
- GPU memory；
- voice encode duration。

发布门槛：

1. 网络 audio chunk 每秒不超过 10。
2. 默认输出约每秒 5～8 个 chunk。
3. 持续播放无明显 segment gap、click、重复或截断。
4. 中断后快速停止后续音频发送；具体 p95 在 Phase 0 基准后固定。
5. 并发 8 下保持明显快于实时。
6. 同一 Voice Profile 跨请求音色稳定。
7. style 切换不明显改变 voice identity。
8. faithful 模式不静默忽略 style。
9. 无 CUDA 时 fail fast。
10. 新主机一键安装后可完成 health、reference clone 和 streaming smoke。
11. 同一 Skill artifact 在三个 runtime 完成 discovery 和固定合成 smoke。

## 26. 仓库结构

```text
botified-tts/
├── pyproject.toml
├── uv.lock
├── README.md
├── src/botified_tts/
│   ├── main.py
│   ├── config.py
│   ├── api.py
│   ├── auth.py
│   ├── contracts.py
│   ├── errors.py
│   ├── capabilities.py
│   ├── audio.py
│   ├── text.py
│   ├── markdown.py
│   ├── styles.py
│   ├── segmentation.py
│   ├── voices.py
│   ├── storage.py
│   ├── sessions.py
│   ├── continuation.py
│   ├── scheduler.py
│   ├── nano_adapter.py
│   ├── openai_api.py
│   └── metrics.py
├── vendor/
│   └── nanovllm-voxcpm/
├── tests/
│   ├── test_api_contract.py
│   ├── test_styles.py
│   ├── test_markdown.py
│   ├── test_segmentation.py
│   ├── test_sessions.py
│   ├── test_continuation.py
│   ├── test_voices.py
│   ├── test_cuda_preflight.py
│   └── live/
├── skills/botified-tts/
│   ├── SKILL.md
│   ├── agents/openai.yaml
│   ├── scripts/botified-tts
│   └── references/
├── deploy/
│   ├── Containerfile.cuda
│   ├── compose.yaml
│   └── botified-tts.service
├── scripts/
│   ├── release
│   ├── live-acceptance
│   └── gpu-smoke
└── docs/
    ├── engineering/
    ├── api.md
    ├── realtime.md
    ├── voices.md
    └── deployment.md
```

不先建立抽象 repository/service/use-case 多层框架。文件超出清晰职责后再按真实
边界拆分。

## 27. 开发阶段

### Phase 0：VoxCPM2/Nano 证据验证

交付：

- 最小 Nano fork；
- generated latent result；
- role-aware latent encoding；
- continuation prototype；
- style reset prototype；
- 真实 GPU A/B 报告；
- 固定音频样本和评估脚本。

GO 条件：

- generated continuation 明显优于简单 waveform 拼接；
- ref-only + style 可用；
- style reset 能切换情绪并保留音色；
- 跨段没有不可接受的边界 artifact；
- Nano fork 可以可靠取消和释放资源。

NO-GO 条件：

- generated continuation 无法稳定工作；
- continuation 导致持续严重文本错读；
- style reset 明显破坏音色；
- Nano 无法可靠回传 latent 或取消；
- 实时 RTF 在目标硬件上不满足播放。

NO-GO 时先回到模型/推理选型，不继续堆产品 API。

### Phase 1：服务基础

交付：

- Python project；
- pinned dependencies；
- CUDA preflight；
- config/auth/health；
- model artifact verification；
- Nano adapter；
- Voice Profile Store；
- reference-only clone；
- Native HTTP generation；
- OpenAI compatibility；
- structured logs/metrics。

### Phase 2：丰富音色

交付：

- faithful clone；
- Voice Design candidate；
- candidate materialization；
- style compiler；
- vocalization parts；
- normalization；
- Markdown；
- WAV/MP3/Ogg/Opus artifacts。

### Phase 3：Realtime

交付：

- session API；
- WebSocket protocol；
- binary audio frames；
- smart segmenter；
- output aggregator；
- generated continuation；
- speculative/committed state；
- playback ack；
- cancel/barge-in；
- backpressure；
- multi-session scheduler。

### Phase 4：生态发布

交付：

- Agent Skill；
- deterministic helper；
- `install-tts.sh`；
- `install-tts-skill.sh`；
- OCI image；
- release manifest；
- OpenAPI artifact；
- Botified bridge；
- Gateway voice-message acceptance；
- 三 runtime Skill acceptance。

这些阶段是实现顺序，不表示允许发布缺失核心能力的半成品。首个正式 release
必须至少完成 Phase 0～4 的首版范围。

### Phase 5：后续增强

- runtime LoRA Voice Profile；
- LoRA training workflow；
- multi-GPU sticky routing；
- vLLM-Omni optional adapter；
- automatic quality evaluation；
- provenance/watermark；
- 更丰富 pronunciation；
- 更细的 style preset catalog。

## 28. 风险

| 风险 | 处理 |
|---|---|
| Nano 是社区项目 | 固定小型 fork、完整 commit、真实 GPU parity |
| continuation 与 style 冲突 | style change reset，Phase 0 A/B |
| generated latent 无文本对齐 | 只按完整 segment 保留 |
| draft token 后续被改写 | bridge 显式区分 draft/committed |
| Voice Design 不稳定 | candidates + materialize |
| reference latent padding 不一致 | role-aware encode |
| 长文本失稳 | server segmenter 和 hard max |
| 短文本弱 | 最小时长缓冲，done 时允许短尾段 |
| 客户端播放慢 | bounded buffers、cancel、client_too_slow |
| 用户中断后上下文错误 | playback ack + speculative rollback |
| 模型升级破坏 voice latent | fingerprint + re-encode |
| 无 CUDA 仍下载模型 | installer 和 startup 双重 preflight |
| OpenAI API 表达力不足 | 仅作为兼容 adapter |
| LoRA 占用和治理复杂 | 首版不做在线训练 |

## 29. Definition of Done

- [ ] 技术报告中的模型模式与官方实现一致。
- [ ] Nano fork 只包含通用推理扩展。
- [ ] generated latents 可稳定回传。
- [ ] reference/continuation 使用正确 padding。
- [ ] Voice Profile 有完整 fingerprint。
- [ ] Voice Design 通过 candidate materialization 固化。
- [ ] expressive 和 faithful 模式明确区分。
- [ ] faithful + style 被显式拒绝。
- [ ] 官方非语言标签通过结构化 parts 使用。
- [ ] Markdown Agent 文本不会朗读控制符和代码块。
- [ ] 分段器通过中英文真实 token stream 测试。
- [ ] realtime 输出每秒不超过 10 个网络 chunk。
- [ ] 跨段 continuation 通过听测和边界测试。
- [ ] style change reset 通过音色和情绪测试。
- [ ] playback ack 和 speculative rollback 正确。
- [ ] slow client 不产生无界队列。
- [ ] cancel 释放 sequence、KV 和 latent buffers。
- [ ] OpenAI SDK 基础 speech smoke 通过。
- [ ] Native REST/OpenAPI/Skill 使用同一契约。
- [ ] Botified preview bridge 显式标识 draft。
- [ ] Ogg/Opus artifact 可通过现有 Gateway 作为 voice intent 发布。
- [ ] 无 CUDA 时下载前失败并输出 stable code。
- [ ] 固定 OCI/model/dependency digest。
- [ ] fresh host 一键安装通过。
- [ ] 三个 Agent runtime 使用同一 Skill artifact。
- [ ] Voice Profile 删除清理全部关联资产。
- [ ] 默认日志、metrics 和错误不泄漏文本、声音或 secret。
- [ ] API、schema、Skill 和错误码均不包含 Voice Profile 授权审计流程。

## 30. 最终产品判断

Botified-TTS 不应被实现成“给 Nano-vLLM 套一个 OpenAI Speech API”。

正确的产品边界是：

```text
VoxCPM2 model
    +
Nano inference engine
    +
Voice Profile
    +
Voice Design materialization
    +
structured style/vocalization
    +
semantic segmentation
    +
generated-latent continuation
    +
full-duplex session protocol
    +
playback-aware cancellation
    +
Agent Skill
    +
Botified token bridge
```

OpenAI compatibility 保证通用客户端可以使用基础 TTS；Botified Native API
和 realtime session protocol 才是 Botified 生态内部的长期能力契约。
