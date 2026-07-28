# Botified TTS 原生 Ogg/Opus 开发计划

> 状态：已收敛，可交付开发
> 目标版本：`v0.2.0`
> 适用范围：当前 `botified-tts` 仓库
> 基线：根目录 `AGENTS.md`、`docs/development-constraints.md`
> 诊断依据：`docs/diagnosis/wav-to-ogg-fluffychat-duration-seek.md`

本文是本轮功能施工计划，不是项目的全局真相。若本文与项目约束冲突，以
`AGENTS.md` 和 `docs/development-constraints.md` 为准。

## 1. 问题

当前 `POST /v1/speech` 只返回 WAV。需要把语音作为消息或文件发布的客户端必须：

1. 下载体积较大的 PCM WAV；
2. 自行安装 FFmpeg；
3. 各自维护 Ogg/Opus 转码参数；
4. 自行判断响应和输出文件是否匹配。

这使多个客户端重复实现同一件事，也容易产生码率、声道、采样率和 MIME
不一致。

现有诊断中的实际样本表明：

| 音频时长 | WAV | Ogg/Opus | 体积减少 | 转码耗时 |
|---:|---:|---:|---:|---:|
| 7.36 秒 | 706 KB | 61 KB | 91.3% | 平均 132 ms |
| 39.68 秒 | 3.81 MB | 327 KB | 91.4% | 平均 361 ms |

服务镜像和本地开发环境已经依赖 FFmpeg。把最终文件封装收回服务端，不需要引入
新的系统或 Python 依赖。

## 2. 目标

- 现有非流式 `POST /v1/speech` 原生返回完整 Ogg/Opus 文件。
- 未选择 Ogg 的现有客户端继续得到完全相同的 WAV。
- Ogg 固定使用已验证的单一参数，不暴露编码调优配置。
- Skill 可以直接生成 `.ogg` 文件，不要求 Agent 主机安装 FFmpeg。
- 输出 MIME 精确为 `audio/ogg`，不附加 `codecs=opus`。
- HTTP WAV 和 Ogg 复用同一个 `SpeechService`、segmenter、推理结果和 PCM
  校验 owner。
- 保持 WebSocket PCM、companion、VoiceStore、模型和部署方式不变。

## 3. 范围边界

### 3.1 本轮包含

- HTTP 完整文件的 WAV/Ogg 内容协商。
- PCM s16le 到 Ogg/Opus 的服务端编码。
- Skill helper 的 `.wav`/`.ogg` 输出。
- README、Skill 说明和当前产品边界更新。
- 根包版本提升到 `0.2.0`，发布固定的 `v0.2.0` 镜像和 Release。

### 3.2 本轮不包含

- WebSocket Ogg、HTTP chunked Ogg 或其他流式封装。
- MP3、AAC、FLAC、裸 Opus 或通用 codec registry。
- 可配置 bitrate、application、frame duration、Ogg page 或 FFmpeg 参数。
- FFmpeg 常驻进程、进程池、独立转码服务、转码缓存或中间文件。
- 新 endpoint、query 参数、JSON `format`/`response_format` 字段。
- `X-Audio-Duration-Ms` 等新的私有响应头。当前 Skill 不消费该信息，Matrix
  gateway 也会从完整文件自行取得时长。
- waveform、Matrix 上传、Matrix 事件构造或最终 Matrix MIME 修正。
- 修改 companion、`../botified`、`../botified-asr`、`.reference/**` 或任何
  上游仓库。
- OpenAI 兼容、Markdown/SSML 处理或其他模型能力。
- 新的 GPU 测试、音色模式测试矩阵、性能测试框架或开发治理产物。

原生 Ogg 解决客户端转码和传输体积问题，但不会自动修正 Botified Matrix
gateway 后续重新探测产生的 `audio/ogg; codecs=opus`。两者是不同 owner，不在
本轮混合处理。

## 4. 唯一公开用法

继续使用同一个 endpoint 和 JSON schema，只通过 HTTP `Accept` 选择最终文件
格式。

| `Accept` | 结果 |
|---|---|
| 缺失 | `audio/wav` |
| `*/*` | `audio/wav` |
| `audio/wav` | `audio/wav` |
| `audio/ogg` | `audio/ogg`，内容为 Ogg/Opus |
| 其他值 | HTTP 406，现有 `invalid_request` 错误结构 |

实现只支持上表中的单一 canonical 值。比较前允许去除首尾空白并转换为小写；
不实现媒体列表、q-value、通配 `audio/*` 或完整的通用 Accept 协商器。

默认 WAV：

```bash
curl \
  -H "Authorization: Bearer ${BOTIFIED_TTS_API_KEY}" \
  -H 'Content-Type: application/json' \
  --data '{"text":"你好。"}' \
  http://127.0.0.1:8000/v1/speech \
  --output speech.wav
```

Ogg/Opus：

```bash
curl \
  -H "Authorization: Bearer ${BOTIFIED_TTS_API_KEY}" \
  -H 'Content-Type: application/json' \
  -H 'Accept: audio/ogg' \
  --data '{"text":"你好。"}' \
  http://127.0.0.1:8000/v1/speech \
  --output speech.ogg
```

Skill 仍只有一个 `speak` 命令，通过 `--output` 后缀决定请求格式：

```bash
"${TTS}" --env-file "${ENV_FILE}" speak \
  --text '你好。' \
  --output speech.ogg
```

只接受小写 `.wav` 和 `.ogg` 后缀。格式已经由输出路径完整表达，不再增加重复的
`--format` 参数。

## 5. 服务端设计

### 5.1 HTTP 选择时机

`src/botified_tts/app.py` 在以下顺序中处理请求：

1. 认证；
2. readiness；
3. 解析 `Accept`；
4. 不支持时立即返回 406；
5. 获取现有 admission 名额；
6. 解析请求、分段并调用一次 `SpeechService`；
7. 用所选 formatter 生成最终完整文件；
8. 返回响应并由现有 `finally` 释放名额。

无效 `Accept` 不占用 admission，也不进入分段、推理或 FFmpeg。成功响应设置
`Vary: Accept`。

### 5.2 单一音频 owner

`src/botified_tts/audio.py` 继续拥有最终音频字节转换：

- WAV 使用现有 `pcm_s16le_chunks_to_wav`；
- Ogg 新增一个 `pcm_s16le_chunks_to_ogg_opus`；
- 两者复用同一个逐 chunk PCM s16le 完整采样校验；
- Ogg formatter 把经过校验的 PCM 直接传给 FFmpeg stdin；
- FFmpeg stdout 是最终响应内容，不创建 WAV 中间文件和临时文件。

`SpeechService`、engine、segmenter 和 continuation 不知道最终容器格式。所有
voice、mode、style 和原生标签自动得到 WAV/Ogg 两种封装，不为每种合成能力
建立第二套路径。

### 5.3 固定 Ogg/Opus 参数

FFmpeg 使用参数数组和 `subprocess.run`，禁止 shell：

```text
ffmpeg
-nostdin
-hide_banner
-loglevel error
-f s16le
-ar 48000
-ac 1
-i pipe:0
-map 0:a:0
-ac 1
-ar 48000
-c:a libopus
-b:a 48k
-application voip
-vbr on
-f ogg
pipe:1
```

不显式设置 `frame_duration`，因为 libopus 默认值已经是 20 ms。编码在现有
threadpool 中运行，避免阻塞 event loop；请求继续占用原有 admission 名额，
因此不增加第二个 semaphore、queue 或 FFmpeg 并发配置。

编码器使用 30 秒内部固定超时。进程启动失败、超时、非零退出或空输出
统一抛出 `AudioEncodingError`；不把命令、路径或 FFmpeg stderr 返回给客户端。
HTTP 将该错误映射为现有 HTTP 500 `engine_error`，消息为
`Speech encoding failed`，不新增公共错误码。

### 5.4 响应

- WAV：保持当前 `Content-Type: audio/wav` 和字节行为。
- Ogg：固定 `Content-Type: audio/ogg`，不得返回
  `audio/ogg; codecs=opus`。
- 两种响应都是完整文件，不改为 StreamingResponse。
- 不增加 Content-Disposition、duration、waveform 或 codec 参数头。

## 6. Skill 设计

`skills/voxcpm-tts/scripts/botified-tts` 的 `speak`：

1. 在发请求前检查输出路径以 `.wav` 或 `.ogg` 结尾；
2. `.wav` 发送 `Accept: audio/wav`；
3. `.ogg` 发送 `Accept: audio/ogg`；
4. 要求响应基础 MIME 与请求格式一致；
5. WAV 保留现有 RIFF/WAVE 最小检查；
6. Ogg 检查以 `OggS` 开始，并在头部包含 `OpusHead`；
7. 继续使用现有同目录临时文件和 hard-link 原子落盘；
8. 继续拒绝覆盖已有文件，失败时清理临时文件。

Helper 不调用本地 FFmpeg，不探测音频时长，不生成 sidecar。Skill 文档把
“只生成 WAV”更新为“生成 WAV 或 Ogg/Opus”，并用 `.ogg` 作为发布语音文件的
示例。

## 7. 文件改动

| 文件 | 改动 |
|---|---|
| `src/botified_tts/audio.py` | 复用 PCM 校验，增加唯一 Ogg/Opus formatter 和错误类型 |
| `src/botified_tts/app.py` | 解析 Accept、选择 formatter、在线程池编码 Ogg、返回精确 MIME |
| `tests/test_audio.py` | 验证一次真实 PCM→Ogg/Opus 集成和失败映射 |
| `tests/test_api.py` | 验证默认 WAV、Ogg 协商、406 和编码失败 |
| `skills/voxcpm-tts/scripts/botified-tts` | 根据输出后缀选择 Accept 并验证响应 |
| `tests/test_skill_helper.py` | 增加一个 Ogg 路径并覆盖 MIME、magic、原子输出 |
| `skills/voxcpm-tts/SKILL.md` | 更新能力描述和 Ogg 示例 |
| `README.md` | 更新简介、endpoint、curl、Skill 和固定镜像版本 |
| `docs/development-constraints.md` | 当前范围从 HTTP WAV 更新为 HTTP WAV/Ogg-Opus |
| `pyproject.toml`、`uv.lock` | 根包版本更新到 `0.2.0`，不增加依赖 |

以下文件不改：

- `src/botified_tts/schemas.py`
- `src/botified_tts/speech.py`
- `src/botified_tts/streaming.py`
- `companions/**`
- `tests/gpu_integration.py`
- `Dockerfile`

## 8. 最小测试

### 8.1 音频 owner

在 `tests/test_audio.py` 使用一份固定短 PCM：

- 输出包含 Ogg 和 Opus 头；
- FFprobe 识别为 Ogg、Opus、48 kHz、单声道；
- FFmpeg 可以完整解码；
- 不比较有损解码后的逐样本值、精确二进制、精确码率、packet 或 page 布局。

同一文件只增加一个编码失败测试，证明 FFmpeg 失败统一成为
`AudioEncodingError`。不测试 subprocess mock 的内部调用顺序。

### 8.2 HTTP owner

在 `tests/test_api.py`：

- 现有默认 WAV 用例保持不变；
- 一个 Ogg 用例证明 `Accept: audio/ogg` 选择 Ogg formatter，并返回精确
  `audio/ogg` 与 `Vary: Accept`；
- 一个无效 Accept 用例证明返回 406 `invalid_request`，且不调用
  `SpeechService`；
- 一个 encoder 失败用例证明返回 500 `engine_error`，现有 admission 名额被
  释放。

API 层不重复 FFprobe、voice mode、分句、summary 或 codec 测试。

### 8.3 Skill owner

在现有 Skill helper 路由测试中增加一个 `.ogg` 请求：

- `.wav` 继续发送 `Accept: audio/wav`；
- `.ogg` 发送 `Accept: audio/ogg`；
- 两种输出都原子落盘；
- 未知后缀、MIME 不匹配和无效 Ogg 不留下目标文件或临时文件。

不为 ordinary、design、controllable 和 faithful 分别复制 Ogg 测试；格式与
合成选项互相独立。

### 8.4 不增加的测试

- 不修改 GPU integration；
- 不增加 companion Ogg 测试；
- 不复制 WebSocket PCM 测试；
- 不增加机器相关的毫秒阈值；
- 不为 FFmpeg、FFprobe 或 libopus 上游能力建立测试矩阵。

开发完成后运行现有根测试：

```bash
uv run pytest -q
```

## 9. 施工顺序

### 阶段一：音频与 HTTP

- 在 `audio.py` 增加 Ogg formatter 和错误收敛；
- 在 `app.py` 增加唯一 Accept 选择；
- 完成音频与 API 最小测试。

阶段结果：HTTP 可以稳定返回 WAV 或完整 Ogg/Opus，默认行为不变。

### 阶段二：Skill 与当前文档

- Helper 根据输出后缀请求并验证对应格式；
- 更新 Skill、README 和开发约束；
- 运行完整普通测试并清理被新实现替代的客户端转码说明。

阶段结果：Agent 直接生成可发布的 `.ogg`，不需要本地 FFmpeg。

### 阶段三：版本与发布

- 根包和 lock 更新到 `0.2.0`；
- 在 clean `v0.2.0` 源码上按现有唯一方式构建
  `ghcr.io/lzjever/botified-tts:v0.2.0`；
- 保持 `v0.1.0`，不移动旧 tag，不删除旧镜像；
- 不创建 Docker `latest`、额外 registry、release asset、workflow 或发布脚本；
- 匿名拉取固定镜像后，用一次短 HTTP Ogg 请求确认镜像内 FFmpeg/libopus 与
  endpoint 可用；
- 创建仅指向固定镜像的简短 GitHub Release。

阶段结果：普通用户可以直接部署包含原生 Ogg 的固定版本镜像。

## 10. 验收标准

- 无 `Accept`、`*/*` 和 `audio/wav` 请求保持现有 WAV 行为。
- `Accept: audio/ogg` 返回 HTTP 200、精确 `Content-Type: audio/ogg` 和完整
  Ogg/Opus 文件。
- Ogg 为 Opus、48 kHz、单声道，可完整解码并可 seek。
- FFprobe 比原 PCM 多约 6.5 ms 的 Opus pre-skip 属正常，不据此判失败。
- 不支持的 Accept 在推理前返回 406 `invalid_request`。
- 编码失败返回 500 `engine_error`，不泄露 FFmpeg stderr，并释放 admission。
- Helper 可以直接生成 `.wav` 和 `.ogg`，不调用本地 FFmpeg，不覆盖已有文件，
  失败不留下临时文件。
- ordinary、Voice Design、两类 clone、style 和原生标签共用同一个输出选择，
  没有按能力复制 formatter。
- WebSocket ready 仍声明 PCM s16le、48 kHz、单声道，binary frame 行为不变。
- Companion、VoiceStore、模型、CUDA preflight、下载来源和部署配置不变。
- `docs/development-constraints.md` 反映 HTTP WAV/Ogg-Opus 的当前产品边界。
- 根测试通过；不要求重复执行完整 GPU 能力验证。
- `v0.2.0` tag、公开 GHCR 固定镜像和 Release 指向同一 clean commit；旧
  `v0.1.0` 保持不变。
