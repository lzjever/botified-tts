# Botified v0.4.47 TTS Skill 与 Companion 更新计划

## 1. 背景

本计划基于 Botified `v0.4.47` 当前实现和文档，更新本仓库中的 `tts`
Skill 与 Botified companion。

Botified 最近与本项目直接相关的变化是：

- `v0.4.45` 引入 `runtime.agents_dir`。该目录下的 `skills/` 是用户 Skill，
  `env.d/` 是每个新 Botified Bash 进程都会加载的全局环境；
- 新的 provider request 会重新发现用户 Skill，新 Bash 会重新加载
  `env.d`，两者正常更新都不要求重启 Core；
- `v0.4.47` 为 Core 启动的 Bash 自动注入
  `BOTIFIED_RUNTIME_DATA_BASE_URL` 和 `BOTIFIED_RUNTIME_DATA_TOKEN`，只用于
  Registry WebSocket 与 File Store 上传/下载；
- Botified 管理的长期 companion 仍应使用 `task_presets`，需要自动启动时把同一个
  preset 放入 `start_on_boot`；
- `stream_text` observer 仍由 companion 通过
  `observe_request -> observe_result -> observe` 配置，且要求
  `llm_text_preview.enabled: true`。

当前 `tts` Skill 已经使用 `env.d`，但 Skill helper 与 companion 还没有形成一致、
轻量且可独立发行的最终形态：

1. Skill helper 同时依赖 Bash、curl、Python 和多项 coreutils，安装前置较多；
2. companion 仍自行解析另一个 env-file，并要求传入完整 WebSocket URL，和
   Botified 已提供的 `env.d` 形成两套 TTS 客户端配置；
3. companion 依赖仓库中的 `sidecar.py` 路径启动，没有稳定的安装后命令；
4. README 和当前约束仍明确要求 companion 使用独立 env-file，已经不符合新的目标；
5. Skill 与 companion 将来可能分别发行，不能通过 Python import 或共享内部模块
   形成代码耦合。

本轮只解决这些集成问题，不改变 TTS 服务 API、VoxCPM2 推理、分段算法或音频格式。

## 2. 产品目标

完成后保留两个职责清楚、互不依赖的入口：

| 使用场景 | 唯一入口 | 结果 |
| --- | --- | --- |
| 用户明确要求生成、下载或发送语音 | `tts` Skill + 自带 helper | 生成 WAV/Ogg 文件，再由 Agent 调用 `publish_file` |
| 有本地扬声器时按需持续朗读 Agent 正在生成的回复 | `botified-tts-companion` | 消费 `stream_text`，增量调用 TTS WebSocket，并在 Botified 主机本地实时播放 PCM |

两个入口共同读取 Botified Bash 环境中的：

```text
BOTIFIED_TTS_URL=http://tts-host:8000
BOTIFIED_TTS_API_KEY=<secret>
```

但它们只共享这两个公开环境变量和已有 TTS API 语义，不共享运行时代码。

## 3. 固定设计

### 3.1 Botified 版本与安装位置

- 本轮面向并验证 Botified `v0.4.47`，文档不再维护旧机制的并行用法；
- Skill 安装到 `<resolved-agents-dir>/skills/tts/`；
- 客户端 URL 和 key 只放在
  `<resolved-agents-dir>/env.d/botified-tts.env`；
- 不把 live credential 写进 Skill、companion 安装目录、preset command、日志或
  文档示例；
- 不在 `env.d` 中定义 Botified 保留的
  `BOTIFIED_RUNTIME_DATA_BASE_URL` 或 `BOTIFIED_RUNTIME_DATA_TOKEN`；
- Skill 或 env 文件更新在下一次新 provider request/Bash 中生效；
- 已运行 companion 保留启动时的环境快照，配置变化后由用户停止并重新启动该
  preset，不增加热更新。

`runtime.agents_dir` 省略时使用 Core 服务账号的 `$HOME/.agents`；设置绝对路径时
直接使用；设置相对路径时从 Botified 配置文件目录解析。Skill 不读取 Botified YAML，
也不自行寻找 Agent root。

### 3.2 Skill helper

保留现有调用形式和文件位置：

```text
skills/tts/scripts/botified-tts

botified-tts health
botified-tts voice-create ...
botified-tts voice-list
botified-tts voice-delete ...
botified-tts speak ...
```

把 helper 原地替换为一个可执行的 Python 脚本，只使用 Python 标准库：

- 不依赖 `requests`、`httpx`、`websockets` 或 companion package；
- 不再要求 Bash、curl、mktemp、ln、rm 或 cat；
- 不增加自己的 `pyproject.toml`、虚拟环境或安装器；
- 继续由 Skill 目录直接携带和调用，安装后只要求 `python3`；
- 保持当前 subcommand 和 flag，不增加兼容 alias 或第二套调用方式。

Helper 继续拥有以下行为：

- `health` 只要求 `BOTIFIED_TTS_URL`；
- 其他命令要求 URL 与 `BOTIFIED_TTS_API_KEY`；
- key 只进入 HTTP Authorization header，不进入命令参数或错误输出；
- `voice-create` 继续要求 `--file` 与 `--filename`，并只接受 WAV、FLAC、MP3；
- `speak` 继续支持默认音色、Voice Design、controllable/faithful profile、
  `style`、WAV 和 Ogg；
- 输出先写同目录临时文件，验证格式后以不覆盖已有路径的方式落盘；
- 请求或验证失败时清理临时文件；
- helper 不调用 Botified File Store，也不调用 `publish_file`。

Skill 文本继续指导 Agent 在 helper 成功后调用 Botified `publish_file`。普通音频使用
匹配的 `audio/wav` 或 `audio/ogg`；语音消息使用 Ogg/Opus、
`audio/ogg` 和 `audio_as_voice: true`。

### 3.3 Companion 配置

删除 companion 的：

- `--env-file`；
- env-file parser；
- `--tts-url`；
- 要求用户维护 checkout 根目录 `botified-tts.env` 的文档。

Companion 直接从启动环境读取与 Skill 相同的
`BOTIFIED_TTS_URL`、`BOTIFIED_TTS_API_KEY`。它不解析 `env.d` 文件；环境注入由
Botified 负责。

`BOTIFIED_TTS_URL` 是 HTTP/HTTPS service base URL。Companion 在内部执行唯一转换：

```text
http://host[:port]  -> ws://host[:port]/v1/speech/stream
https://host[:port] -> wss://host[:port]/v1/speech/stream
```

允许一个结尾 `/`，拒绝 userinfo、query、fragment 和其他 path。不要同时保留
完整 WebSocket URL 参数。

音色和表达仍是非敏感、进程期固定的 companion 参数：

```text
--voice-id <voice_id> [--mode controllable|faithful] [--style <text>]
--design <description> [--style <text>]
```

默认模式、互斥关系和 faithful/style 限制继续与 TTS API 一致。不新增运行时切换
音色、控制协议或配置文件。

### 3.4 Companion 独立发行边界

将 companion 保持为 `companions/botified/` 下的独立轻量 Python project，并提供
安装后命令：

```text
botified-tts-companion
```

具体要求：

- distribution 名和 CLI 名统一为 `botified-tts-companion`；
- 使用自己的 `pyproject.toml`、lock file、source package 和测试；
- 运行依赖只保留 WebSocket client 所需的 `websockets`；
- 不依赖根 `botified-tts` Python package、CUDA、Torch、Skill helper 或 Skill
  目录；
- 不从 `skills/tts/**` import；
- Skill helper 也不从 `companions/botified/**` import；
- Botified stdio observer frame 的最小解析由 companion 自己拥有，不依赖
  `botified_playground` 或 Botified 源码 package；
- Skill 与 companion 可以在不同版本、不同虚拟环境和不同主机独立安装。

这次只建立真实可安装的 companion CLI，不同时设计 PyPI 发布流水线、自动安装器、
插件市场或跨平台音频后端。

### 3.5 Companion 运行方式

Botified 管理的正式运行入口只保留 task preset。默认只注册 preset，不自动启动：

```yaml
task_presets:
  presets:
    botified-tts:
      description: Speaks live assistant text through Botified TTS.
      command: "/opt/botified-tts-companion/.venv/bin/botified-tts-companion --voice-id voice_0123456789abcdef0123456789abcdef --mode controllable --style 'calm and conversational'"
  start_on_boot: []
```

规则：

- command 中不出现 URL、API key 或 env-file；
- preset 使用默认 interactive stdio，不能设置 `interactive_stdio: false`；
- companion 是带本地扬声器的 Botified 主机可选能力，不是 TTS 服务的组成部分；
- 默认 `start_on_boot: []`，由 Agent 或操作员需要本地朗读时通过 preset 启动；
- 只有某个部署明确要求每次 Core 启动后都持续本地朗读时，操作员才把该 preset
  加入 `start_on_boot`；
- companion 保持前台运行，由当前 Core 管理生命周期；
- `start_on_boot` 即使显式启用也只负责创建 task，不提供 restart policy 或跨 Core
  存活；
- Core shutdown、task cancel 或 stdin EOF 时取消当前 TTS 和播放后退出；
- 需要跨 Core 存活的部署仍由外部 supervisor 负责，不增加第二套 companion
  daemon 模式。

本轮继续使用 Linux `/usr/bin/aplay` 播放 48 kHz mono PCM s16le。音频设备选择、
远程播放、PulseAudio/PipeWire 抽象和多播放器插件不进入范围。

### 3.6 Observer 与流式行为

保留现有经过测试的流式模型：

1. companion 输出一个 `stream_text` `observe_request`；
2. 收到成功的同 ID `observe_result` 后才处理观察事件；
3. 快速持续读取 stdin，并把 TTS/播放工作放在独立异步任务中；
4. 同一个 assistant observation 的连续文本 chunk 按
   `id/provider_request_id/chunk_index` 重新组装后发送 `append`；
5. assistant `done` 发送 `finish`；
6. assistant `error`、新 user text、新 provider request、stdin EOF 或 task
   cancel 取消当前 TTS session 和播放；
7. `preview_disabled` 明确失败，不回退到 `final_text`；
8. companion 不向 observer frame 回复 ack，也不把日志写成 Botified 协议 frame。

`min_batch_chars` 继续使用 `1`。这里的 frame batching 不等于 TTS 音频 chunk
粒度；服务端已有 sentence-aware segmentation 和受限 PCM 输出。没有实际延迟或负载
问题前，不新增 companion batching 参数。

### 3.7 Runtime Data 与 File Store 的边界

本轮不使用 `BOTIFIED_RUNTIME_DATA_BASE_URL` 或
`BOTIFIED_RUNTIME_DATA_TOKEN`。

原因：

- companion 当前产品结果是本机实时播放，不是一个完整待交付文件；
- Runtime Data 上传只创建 File Store object，不会发布文件、发送消息或唤醒 Agent；
- caller-facing 文件仍必须由 Agent 调用 `publish_file`；
- Skill helper 已在 Botified runtime cwd 生成文件，可直接走现有
  `publish_file`，先上传 File Store 只会增加一次重复传输。

因此不增加 PCM 缓存、自动封装 Ogg、File Store 上传、`tell` 回调或 companion
自动发语音消息。用户要发送语音时使用 `tts` Skill；用户要实时听回复时使用
companion。

## 4. 非目标

本轮不做：

- 修改 `../botified`、`../botified-asr`、`.reference/**` 或任何上游仓库；
- 修改 TTS HTTP/WebSocket API；
- 修改 VoxCPM2、Nano-vLLM、分段、voice anchor 或音频编码；
- Markdown、SSML 或回复文本清洗 parser；
- 把 Skill 和 companion 合并成一个 package；
- 为两者创建 shared/common/client SDK；
- 让 Skill 安装或修改 Botified YAML；
- companion 自动发布语音消息或上传 File Store；
- 旧 companion env-file/URL 参数兼容层；
- companion 热重载、自动重连策略平台、守护进程或通用播放器框架；
- PyPI/GitHub Release 自动发行、安装脚本或版本协商；
- 为 Runtime Data 增加新的权限、scope 或 token 管理。

## 5. 代码改动

| 文件 | 改动 |
| --- | --- |
| `skills/tts/scripts/botified-tts` | 原地改为只使用 Python 标准库的可执行 helper，保持现有 CLI |
| `skills/tts/SKILL.md` | 把前置要求收敛为 `python3`；保持能力选择、纯文本、原生标签、长度和发布指导 |
| `tests/test_skill_helper.py` | 用本地测试 HTTP server 验证 helper 拥有的客户端行为，删除 fake curl 与 shell/coreutils 假设 |
| `companions/botified/pyproject.toml` | 改为可安装的独立 `botified-tts-companion` package 和同名 console command |
| `companions/botified/uv.lock` | 只按 companion project 的实际依赖更新 |
| `companions/botified/sidecar.py` 或新的最小 source package | 删除 env-file/URL CLI，读取环境并从 HTTP base 推导 WebSocket endpoint |
| `companions/botified/tests/test_sidecar.py` | 改测环境配置、URL 推导、installed CLI 和既有 observer/TTS/playback 行为 |
| `companions/botified/README.md` | 改为独立安装命令、统一 env.d、preset 和配置生效说明 |
| `README.md` | 更新 Botified 版本、Skill 前置、companion 安装与 preset；删除 companion 独立 env-file 说法 |
| `docs/development-constraints.md` | 固化 Skill/companion 环境入口与代码隔离；删除已过时的 companion env-file 约束 |

历史施工计划只保留历史，不为清空旧关键词而批量改写。

## 6. 开发顺序

### 阶段一：Skill helper 去除非标准库依赖

1. 先把 helper 测试从 fake curl 改为本地 HTTP server；
2. 测试保持现有 subcommand、JSON、multipart、认证、格式验证和不覆盖输出行为；
3. 用 Python 标准库原地实现 helper；
4. 删除 Bash/curl/coreutils 特有实现和测试；
5. 同步 Skill 与 README 的前置要求。

本阶段不修改 TTS 服务端，也不为 Python helper 建立 package。

### 阶段二：Companion 统一 Botified 环境

1. 用测试表达 URL/key 只来自进程环境；
2. 增加 HTTP/HTTPS base 到 WS/WSS endpoint 的唯一转换；
3. 删除 env-file parser、`--env-file` 和 `--tts-url`；
4. 保持 voice/design/mode/style 和 observer 流式逻辑不变；
5. 删除只覆盖旧 env-file 格式的测试，不把它们迁移成第二套配置测试。

### 阶段三：Companion 独立安装

1. 将 companion 变成 pyproject 可安装 package；
2. 提供唯一 console command `botified-tts-companion`；
3. 更新 lock file；
4. preset 只调用安装后绝对 CLI 路径；
5. README 说明 `env.d` 由 Botified 注入，运行中的 task 需要重启才读取新值。

不要让 companion import Skill helper 来复用 URL 或 API 请求代码。相同的两个环境变量
名称属于公开接口，不构成需要抽取 shared module 的代码重复。

### 阶段四：当前文档同步

只更新 `README.md`、companion README、Skill 和开发约束中的当前用法：

- Botified 目标版本为 `v0.4.47`；
- Skill host 只要求 `python3`；
- Skill 与 companion 共用 Agent `env.d` 中的客户端 URL/key；
- Docker 服务自己的 env-file 仍只配置服务进程，不与 Agent `env.d` 混为一谈；
- companion preset 不携带 secret、URL 或 env-file，默认不在 `start_on_boot`
  中启动；
- Runtime Data 不用于 TTS 文件交付；
- Skill 负责可发布文件，companion 负责本机实时播放。

## 7. 最小测试范围

### 7.1 Skill helper

保留一组面向 helper 公开行为的测试：

- 缺少 URL/key 时失败且不泄露 key；
- `health` 不要求 key；
- voice list/delete 的方法、路径和 Authorization 正确；
- voice create 的 multipart 字段与固定安全 filename 正确；
- speak 的 voice/design/mode/style JSON 正确；
- WAV/Ogg 响应验证、临时文件清理和拒绝覆盖已有输出正确；
- 非 2xx、错误 content type 和坏音频返回失败。

不重复测试服务端已经覆盖的 VoxCPM2、分段、音频编码或完整 API schema。

### 7.2 Companion

在现有测试基础上保留：

- 缺少或非法环境变量明确失败且不打印 key；
- HTTP/HTTPS base 分别推导 WS/WSS endpoint；
- userinfo/query/fragment/额外 path 被拒绝；
- voice/design/mode/style 组合验证；
- observer 配置成功/失败；
- 文本 chunk 重组、append、finish、provider replacement 和 user interrupt；
- stdin EOF、WebSocket error、播放器提前退出和取消清理；
- 安装后的 `botified-tts-companion` 命令可启动。

删除 env-file 内容解析测试和完整 `--tts-url` 参数测试。它们对应的产品入口已经删除，
不保留兼容行为。

### 7.3 实际联调

只做两条端到端用户路径：

1. 安装 Skill 和 `env.d`，在新的 Botified request 中生成 Ogg，并通过
   `publish_file` 作为普通音频或 voice message 交付；
2. 在带本地扬声器的 Botified 主机上按需从 preset 启动 companion，确认流式回复
   能够边生成边播放，并在用户新输入时停止当前播放。

第一条验证文件交付，第二条验证实时朗读；不把同一行为在两条路径重复测试。

## 8. 验收标准

- Botified `v0.4.47` 的 Agent root 中只有一份 TTS 客户端 URL/key 配置；
- Skill 和 companion 都能从新 Bash/task 的环境读取该配置；
- Skill helper 运行时只需要 Python 标准库，不依赖第三方 Python package、curl 或
  companion；
- Skill helper 的现有 subcommand 和合成能力保持可用；
- 生成文件仍由 Agent 使用 `publish_file` 交付，Ogg voice message 使用
  `audio/ogg` 与 `audio_as_voice: true`；
- companion 有独立安装后的 `botified-tts-companion` 命令；
- companion 运行依赖不包含根 TTS 服务、CUDA/Torch、Skill 或 Botified 源码包；
- companion preset command 不包含 URL、API key 或 env-file；
- companion preset 默认不在 `start_on_boot` 中，普通 TTS 服务部署不会自动打开
  本地播放；
- companion 保持双向流式衔接、用户打断、provider replacement 和错误清理；
- Skill 与 companion 之间没有 Python import、文件路径调用或共享内部 package；
- 当前 README、Skill、companion README 和开发约束不再指导旧 env-file/URL
  companion 用法；
- 未修改任何参考仓库，未增加 shared SDK、File Store 旁路或新的治理设施。
