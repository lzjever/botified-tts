# Botified TTS 开发约束

> 状态：Current truth

本文约束本项目的代码、测试、文档和部署实现。它不是评审流程、交付清单或证据
要求。若其他开发计划与本文冲突，以本文为准。

## 1. 基本原则

1. 始终遵循 KISS、DRY、YAGNI。
2. 一个功能只有一种公开用法、一个状态模型和一个实现 owner。
3. 优先完成当前 Botified TTS 的明确需求，不为假设中的未来场景预留平台能力。
4. 发现问题后直接在当前实现中就地修正，并添加能证明用户可见行为的最小测试。
5. 发现不必要的抽象、兼容层、配置、文档或开发治理负担时，在当前变更中直接
   删除，不保留“以后可能有用”的旧路径。

## 2. 严禁引入开发治理负担

除非用户针对一项真实业务需求明确要求，否则不得创建：

- 合约清单、兼容性台账或接口治理框架；
- evidence、证明包、审计材料或验收证据目录；
- 进度报告、完成报告、handoff 报告或阶段性 sidecar；
- review gate、release gate、质量门禁框架或 gate 的 gate；
- 通用 checklist、评分体系、成熟度模型或风险打分系统；
- 为记录过程而存在的 manifest、fingerprint、attestation 或追踪数据库；
- 对当前内部项目没有直接用途的权限、审批、审计和多租户体系；
- 为开发流程服务而不是为 TTS 产品服务的后台、服务或自动化平台。

固定依赖版本应直接写入 `uv.lock`、Dockerfile、模型 revision 等实际运行来源，
不得再复制成一套“证据清单”。

必要的 README、API 使用说明、部署说明和当前产品边界可以保留，但只记录使用和
实现所需的当前事实，不记录开发过程。

## 3. 问题处理方式

发现 bug、性能问题或设计错误时：

1. 找到当前唯一 owner。
2. 在该 owner 中修正根因。
3. 修改或增加最小的行为测试。
4. 删除被替代的旧实现、fallback、临时 guard 和无效文档。

不得为了修一个具体问题增加第二套 pipeline、通用 policy engine、恢复框架、
兼容 shim、旁路状态或重复防线。

内部实现默认原地替换。没有明确的外部兼容或持久数据要求时，不增加 `v2`、
alias、双写、迁移框架或长期 deprecated 路径。

## 4. 代码与架构

- HTTP 与 WebSocket 必须复用同一个 `SpeechService`。
- 长文本和增量文本必须复用同一个 segmenter。
- 只使用 VoxCPM2 和 Nano-vLLM-VoxCPM，不建立通用模型插件系统。
- Botified 对接只使用本仓库内的薄 companion；它使用独立轻量依赖，不加入根
  Python package 或根 uv workspace，也不引入 Torch/CUDA。
- Docker、Skill helper 和 companion 共用同一份 `botified-tts.env`；helper 与
  companion 只安全解析其中恰好一条 API key。token 必须是不加引号的
  `[A-Za-z0-9._~-]+`，按第一个 `=` 后的字面值读取，不 quote、interpolate 或
  source shell。helper 的 URL 只用 `BOTIFIED_TTS_URL`，companion 的 URL 只用
  `--tts-url`。
- 固定模型 spec 只保存 repo ID 和 revision；模型 cache 只从 `Settings` 的
  data dir 与 model source 推导，不保存第二份路径配置。
- 只保留当前产品需要的 VoiceStore、合成、分段、音频和部署模块。
- 只有出现真实重复或明确 ownership 问题时才增加抽象。
- 不为目录整齐而预拆 domain、repository、service、use-case 等层级。
- 不重复实现 Nano、FastAPI、CUDA 或音频库已经可靠拥有的功能。
- 配置只暴露用户确实需要改变的值；内部调优常量留在唯一源码位置。

## 5. 测试

- 测试本项目拥有的用户行为、协议边界和资源上限。
- bug 修复测试应先能复现问题，再证明修正后的外部结果。
- 不测试测试脚本、fixture、mock 调用顺序、报告生成器或 gate 是否存在。
- 不重复测试上游框架和推理库已经覆盖的内部行为。
- 不为假设场景建立大规模参数、语言、并发或故障矩阵。
- 按风险选择最小验证范围；需要 CUDA 事实时使用一份可选运行的真实 GPU
  integration，不用复杂模拟器代替硬件结论。
- 同一个行为不得在 unit、service integration 和 GPU integration 中重复建设
  多套等价测试。

## 6. 范围控制

首版只服务于稳定、方便的 Botified TTS：

- HTTP 非流式 WAV 或 Ogg/Opus 完整文件；
- WebSocket 增量文本输入与 PCM 输出；
- VoxCPM2 原生 Voice Design、音色克隆、style 和非语言标签；
- 服务端分段与跨段 continuation；
- 最小音色创建、列表和删除；
- CUDA-only；普通用户使用公开的固定版本镜像、私有 env-file 和唯一
  `docker run`；
- 本仓库内的最小 Botified companion；
- 最小 Agent Skill。

OpenAI 兼容、Artifact/Job、多 GPU 调度、多租户治理、复杂播放状态、自动质量
平台、LoRA、Compose、部署脚本和其他模型不因“将来可能需要”进入项目。

新增范围必须来自明确业务需求。实现过程中发现任何偏离本边界的代码或文档，
立即原地清理。

当前仓库之外的 Botified、Botified ASR、上游和参考仓库均为只读；所有适配和
集成实现都放在当前仓库。完整写入边界见根目录 `AGENTS.md`。
