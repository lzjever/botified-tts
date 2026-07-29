# TTS Skill 通用化与 env.d 迁移开发计划

> 状态：待开发
>
> 适用范围：当前 `botified-tts` 仓库的一次原地改进
>
> Botified 基线：`v0.4.45` 或更高版本

## 1. 问题与目标

当前 Skill 存在五个直接影响使用的问题：

1. `voxcpm-tts` 暴露底层模型，而用户需要的是稳定的 TTS 能力；
2. README 和 Skill 仍要求 `skills.explicit` 指向 repo checkout，没有使用最新版
   Botified 的 `<resolved-agents-dir>/skills`；
3. helper 主动清除继承的 API key，强制解析 `--env-file`，与 Botified
   `env.d` 的 Bash 注入机制冲突；
4. Skill 只报告生成文件的本地路径，没有使用 `publish_file` 完成普通文件或语音
   消息交付；
5. Botified file ref 的 `agent_path` 没有原文件扩展名，当前 helper 又从
   `--file` 路径生成 multipart filename，导致合法参考音频无法创建 profile。

本轮交付后，Botified Agent 通过唯一的 `tts` Skill 使用现有 Botified TTS
服务。Skill 安装在 resolved Agent root，helper 只消费进程环境，面向调用方的
音频通过 Botified 已有的 `publish_file` 发布。

## 2. 范围

### 2.1 本轮完成

- 将 `skills/voxcpm-tts` 一次性改名为 `skills/tts`；
- 补齐面向 TTS、朗读、语音消息、音色克隆、Voice Design、情绪语气、非语言标签、
  多语言和方言的 Skill metadata 与使用指导；
- 将 Skill 的唯一安装位置改为 `<resolved-agents-dir>/skills/tts`；
- 将 Skill 客户端的唯一配置入口改为
  `<resolved-agents-dir>/env.d/botified-tts.env`；
- 删除 helper 的 `--env-file` 和 env-file parser，直接读取
  `BOTIFIED_TTS_URL`、`BOTIFIED_TTS_API_KEY`；
- 让 `voice-create` 分别消费 manifest `agent_path` 和 `filename`，按 filename
  suffix 生成固定且安全的 multipart filename；
- 保留普通合成、Voice Design、两种克隆、音色管理和 WAV/Ogg 输出；
- 在 Skill 工作流中加入普通音频发布和 Ogg 语音消息发布；
- 原地调整现有 helper 测试、README 和 `docs/development-constraints.md`。

### 2.2 明确不做

- 不修改当前仓库外的 Botified、Botified ASR、参考或上游仓库；
- 不修改 TTS 服务、HTTP/WebSocket API、VoxCPM2、Nano-vLLM、分段、音频编码、
  Docker 镜像或 companion；
- 不因本次 Skill 改动构建或发布 TTS Docker 镜像；
- 不增加多 TTS provider、backend/plugin 抽象、OpenAI 兼容或通用 TTS CLI；
- 不把 VoxCPM2 上游的 `cfg_value`、`inference_timesteps`、`seed`、`normalize`、
  `denoise`、`retry_badcase`、音素、LoRA、batch 或 timestamps 暴露成新服务参数
  或 helper flags；
- 不增加安装器、部署脚本、配置向导、自动 Agent root 探测或 secret manager；
- 不保留旧名称、alias、symlink、兼容 Skill 或 `--env-file` fallback；
- 不在 helper 或服务中实现发布，也不修改或验证渠道的原生语音兼容性；
- 不测试 Botified 已拥有的 env.d parser、权限、刷新或 Skill 发现优先级；
- 不为本次工作增加测试框架、治理文件、报告或发布 gate；
- 不批量改写已完成的 `docs/engineering/*` 历史施工计划。

## 3. 固定设计

### 3.1 命名

| 对象 | 名称 |
| --- | --- |
| Skill 目录和 frontmatter | `tts` |
| Skill helper | `scripts/botified-tts` |
| 仓库、服务、镜像和 API | `botified-tts` |
| 客户端环境变量 | `BOTIFIED_TTS_URL`、`BOTIFIED_TTS_API_KEY` |
| Skill 客户端 env 文件 | `env.d/botified-tts.env` |
| 底层模型 | VoxCPM2，仅在能力和实现说明中出现 |

`botified-*` 是 Botified 官方 Skill 的保留命名空间，不能作为本 Skill 名称。
`TTS_URL`、`TTS_API_KEY` 过于通用，也不得使用。

单个 Core 的全部 Skill 发现根合计只能加载一个 `name: tts`。Botified 不会按
发现根优先级决定非官方同名 Skill；重复加载会使 `$tts` 和结构化名称调用产生
歧义。

### 3.2 安装与配置

Resolved Agent root 按 Botified 的真实规则确定：

- 未配置 `runtime.agents_dir`：Core 服务账户的 `$HOME/.agents`；
- 绝对 `runtime.agents_dir`：直接使用；
- 相对 `runtime.agents_dir`：从 Botified 配置文件所在目录解析，不从 runtime
  cwd 或操作员 cwd 解析。

唯一布局为：

```text
<resolved-agents-dir>/
├── skills/
│   └── tts/
│       ├── SKILL.md
│       └── scripts/botified-tts
└── env.d/
    └── botified-tts.env
```

运行前置固定为 Botified Core `v0.4.45+`，启用 Botified 内置 `bash`，并在 Core
主机提供 Bash、curl、`python3` 和 helper 已使用的 GNU coreutils。安装必须保留
`scripts/botified-tts` 的 executable bit。Skill host 只是轻量 HTTP 客户端，不
需要 CUDA、Torch 或 FFmpeg；这些不是 Skill 安装项，也不改变 TTS 服务自身的
运行要求。只有部署已启用 `bash` 时，常规 Skill 安装才不需要修改 Botified YAML。

Env 文件只放 Skill 客户端需要的配置：

```dotenv
BOTIFIED_TTS_URL=http://tts-host:17771
BOTIFIED_TTS_API_KEY=replace_with_actual_key
```

不复制服务端的模型来源、日志、CUDA 或分段配置。文件使用 Botified 的字面
`NAME=VALUE` 格式，不使用 `export`、引号、插值或 shell 语法。Agent root、
`env.d` 和 env 文件必须由 Core effective uid 或 root 持有，且 group/other
不可写；由 Core 账户持有时推荐目录 `0700`、文件 `0600`。root 持有时仍必须让
Core 能 traverse 目录和读取文件。

URL 和 key 放在同一个文件，通过同目录非 `.env` 临时文件原子替换。两个变量在
整个 `env.d/*.env` 合集中各只能定义一次；重复、malformed、unsafe 或 unreadable
输入会让当前 Bash 在命令执行前整体失败，不使用 partial 或旧快照，修正后下一次
Bash 恢复。

`env.d` 是授予所有 Core Bash 进程的全局环境，不按 Skill 隔离，也不是 secret
manager。Skill 不要求用户在聊天或命令参数中提供 key，不把 key 放进仓库或 Skill。

Helper 不查找、读取或解析 `env.d`，也不解析 Botified YAML。开发者从 repo 直接
运行 helper 时，直接 export 相同的两个变量。

### 3.3 Helper

保留 `health`、`voice-create`、`voice-list`、`voice-delete` 和 `speak`。删除
`--env-file`、`ENV_FILE` 及其 Python parser，不增加第二个配置入口。

`voice-create` 的唯一文件输入为：

```text
voice-create --name <profile-name> --file <agent_path> --filename <manifest.filename>
```

`--file` 提供可读的文件字节，`--filename` 对所有 `voice-create` 调用必填；不从
`--file` basename 推导，也不保留旧 fallback。Helper 只按大小写不敏感的 suffix
接受 `.wav`、`.flac` 或 `.mp3`，并分别使用固定 multipart filename
`reference.wav`、`reference.flac` 或 `reference.mp3`。Manifest 原始 filename
不进入 curl form 表达式，也不保存为 profile metadata；中文、空格、逗号、分号、
引号、反斜杠等字符只要最终 suffix 有效就不导致失败。Helper 不复制输入，不创建
临时副本，也没有成功、失败或取消后的输入清理流程。

缺少 `--filename` 时在网络请求前报告 `--filename is required`；无 suffix 或
`.ogg` 等不支持的 suffix 报告
`--filename must end with .wav, .flac, or .mp3`。除既定删除全局
`--env-file` 外，必填 `--filename` 是本轮唯一新增的 breaking CLI change；
其他命令和参数保持不变。

Helper 启动时把 `BOTIFIED_TTS_API_KEY` 复制到进程内变量，随后从导出环境清除
原变量，避免继续传给 `curl`、`python3` 等子进程。请求前验证 URL 非空且使用
HTTP(S) scheme；认证请求还验证 key 存在并匹配 `[A-Za-z0-9._~-]+`。其他 URL
解析或连接错误由 curl 正常返回，不增加 URL parser。Bearer header 继续通过
curl stdin 传递，不放入 argv、stdout 或 stderr。

`health` 是公开请求，只要求 URL；其他命令要求 URL 和 key。现有参数校验、HTTP
请求、MIME/音频验证、已有文件不覆盖和原子输出行为保持不变。

### 3.4 Skill 工作流

Frontmatter 使用：

```yaml
---
name: tts
description: Use the configured Botified TTS service for multilingual speech synthesis, voice design and cloning, expressive style and VoxCPM2 nonverbal tags, WAV/Ogg publication, and trusted voice profile management.
when_to_use: TTS; text to speech; 文字转语音; 朗读; 发送或回复语音; 音色设计或克隆; 情绪、语速、语气词、笑声或停顿; 多语言或方言语音; 管理音色
---
```

Skill 正文保持简短，但必须给 Agent 足够的模式选择信息：

| 用户意图 | 唯一做法 |
| --- | --- |
| 不指定声音，只需朗读 | `speak`，不传 `--design` 或 `--voice-id`；需要情绪、语速或表达时可选 `--style` |
| 从描述创造新声音 | `--design` 描述身份与声音质感；需要时用 `--style` 描述表达状态 |
| 保留已有音色并改变情绪、语速或表达 | `--voice-id`，使用默认 controllable mode，并按需传 `--style` |
| 最大程度延续参考音频的音色、节奏、情绪和风格 | 使用带精确转写的 profile，加 `--mode faithful`，不得传 `--style` |
| 加入笑声、叹息、迟疑、疑问或惊讶等局部表达 | 将官方非语言标签直接放进 `--text`，不增加新参数 |

用户提供参考音频时，Botified file ref 必须同时为 available 并包含
`agent_path` 和 `filename`。Agent 将只读 `agent_path` 直接传给
`voice-create --file`，并将 manifest `filename` 原样传给必填的
`--filename`；manifest unavailable 或没有 `agent_path` 时不调用 helper，请用户
重新上传。`agent_path` 可以是绝对路径；runtime cwd 内相对路径的限制只适用于准备
通过 `publish_file` 交付的新生成音频。创建 profile 后读取返回 JSON 的 `id`，
向用户报告，并在后续合成中作为 `--voice-id`。

用户按名称指定已有 profile 时先调用 `voice-list`：没有匹配或出现重名时不得猜测
ID，应让用户确认。Controllable mode 可使用没有转写的 profile；faithful mode
所需的 `prompt_text` 必须是参考音频的精确逐字转写。没有精确转写时不得猜测、
伪造或在本 Skill 中调用 ASR；改用 controllable mode，或请用户提供准确文本。

普通默认声音和 Voice Design 都不承诺跨请求保持同一音色；需要多次复用稳定身份时
创建并使用 voice profile。

### 3.5 文本与表达指导

VoxCPM2 官方支持以下 30 种语言，通常直接使用目标语言正文，不加语言标签：
阿拉伯语、缅甸语、中文、丹麦语、荷兰语、英语、芬兰语、法语、德语、希腊语、
希伯来语、印地语、印尼语、意大利语、日语、高棉语、韩语、老挝语、马来语、
挪威语、波兰语、葡萄牙语、俄语、西班牙语、斯瓦希里语、瑞典语、菲律宾语、
泰语、土耳其语和越南语。

中文方言包括四川话、粤语、吴语、东北话、河南话、陕西话、山东话、天津话和
闽南话。方言场景应使用地道的方言词汇和写法，而不是给普通话正文只加一个方言
名称；Control Instruction 中的方言要求应保持简单，避免与大量音色形容词互相
干扰。Voice Design 和 style 指令可使用中文或英文。

标点是韵律提示：句号和问号通常形成更清晰的句末停顿，逗号形成较短停顿，省略号
可以表达迟疑或拖尾。需要强停顿时优先把可朗读文本写成更短的自然句，不堆叠标点。
极短的单词或短句可能听起来偏弱；有连续内容时可组织成自然句，但不得擅自添加
填充词或改变用户原意。

服务不暴露上游 `normalize` 参数，因此数字、日期、单位和缩写在发给 helper 前就
应写成目标语言中预期朗读的形式；只消除发音歧义，不改变姓名、金额、日期或其他
事实。不要为解决发音问题虚构音素 flags。

VoxCPM2 官方当前给出的稳定推荐集合是以下 11 个，大小写和拼写必须保持：

```text
[laughing] [sigh] [Uhm] [Shh]
[Question-ah] [Question-ei] [Question-en] [Question-oh]
[Surprise-wa] [Surprise-yo] [Dissatisfaction-hnn]
```

其中 `[laughing]`、`[sigh]` 用于笑声和叹息，`[Uhm]`、`[Shh]` 用于思考或
停顿，上述四个 Question 标签用于疑问语气，两个 Surprise 标签和
`[Dissatisfaction-hnn]` 用于惊讶或不满。

这 11 个是官方建议“尽量只用”的推荐集合，也是本服务 segmenter 保证作为整体
保护、不在标签内部切分的集合；它们不是模型可能学到的所有非语言表达的封闭词表。
官方没有提供可查询的完整标签词典，也没有承诺其他方括号写法稳定可用。其他写法
会作为普通文本进入模型，可能被朗读、忽略或产生不稳定结果，因此 Skill 不主动
使用或宣传支持，服务也不额外增加拒绝规则。

标签应放在希望发生相应表达的位置，只在用户意图或文本语境确实需要时少量使用。
不要创造近义标签、改变大小写，或在一句中叠加很多标签。Helper 不解析或转换
标签；服务的 segmenter 负责在增量输入和内部切句时保持完整标签不被切开。

Voice Design 的 `--design` 应用一条简洁且不矛盾的描述组合：

- 身份：性别、年龄、角色；
- 声音质感：音高、厚薄、沙哑、磁性等；
- 使用场景：播报、旁白、对话等。

`--style` 只描述本次表达，例如情绪、语速、音量和节奏。可控克隆不能任意改变
说话人的身份；它是在保留音色的前提下调整表达。Voice Design 和 style 的遵循
效果可能波动，Skill 不承诺每个属性都能精确实现。

创建 voice profile 时，服务允许 3–60 秒的 WAV、FLAC 或 MP3；Skill 推荐官方更
实用的 5–30 秒、单人、干净、稳定且背景噪声较少的参考音频。需要多次保持同一
音色时使用 profile，不依赖无参考音频的普通合成来维持身份一致。

### 3.6 合成与交付工作流

正文还必须规定：

1. 只处理已经适合朗读的纯文本，不解析 Markdown 或 SSML；
2. 根据意图选择普通合成、Voice Design、controllable clone 或 faithful clone；
3. 不超过 8192 UTF-8 bytes 的一次完整话语只调用一次 helper；服务内部负责长文本
   切句和跨段声音锚定，不为“模型稳定”在 Agent 侧手工拆成多次独立合成。文本超过
   限制或服务返回 `input_too_large` 时，明确报告限制，让用户缩短文本，或明确确认
   拆成多个独立文件；未经确认不得截断或拆分。用户确认后按自然边界拆分并编号，
   每份不超过限制，复用已选择的 voice/mode/style/design options，分别生成和
   `publish_file`，并明确不承诺跨文件的音色、韵律或听感连续性；
4. 准备发布的文件直接生成到 runtime cwd 内的新相对 `.wav` 或 `.ogg` regular
   file，不覆盖已有文件，不使用绝对路径、`..`、cwd 外路径或 symlink；
5. 面向调用方的结果必须调用 `publish_file`，不能只报告服务端路径；
6. 普通附件使用匹配格式的 `audio/wav` 或 `audio/ogg`，省略
   `audio_as_voice`；
7. “发送/回复语音消息”默认生成 Ogg/Opus，并调用：

```json
{
  "path": "reply.ogg",
  "filename": "reply.ogg",
  "mime_type": "audio/ogg",
  "audio_as_voice": true
}
```

未指定格式的普通附件也默认使用较小的 Ogg/Opus，用户明确要求时才使用 WAV。
`audio_as_voice: true` 只是渠道展示意图，不承诺原生语音呈现。

Metadata 应明确它用于 TTS，不用于 ASR、分析或播放已有音频。自然语言触发是
Agent 体验，不作为确定性路由 contract；显式 `$tts` 必须无歧义可用。Token
streaming 和实时播放继续由本仓库 companion 负责。

Agent 只能使用 helper 已有参数。上游 VoxCPM2 还提供 CFG、扩散步数、seed、文本
规范化、降噪、bad-case retry、音素输入、LoRA 和 batch 等能力，但本服务没有把
它们作为请求能力暴露；Skill 不得虚构对应 flags、环境变量或 API 字段。

这些未公开参数继续属于服务内部或上游边界。本轮不为它们预先设计 API、helper
flags、环境变量、兼容规则或测试。

本节能力依据为 VoxCPM2 官方
[使用指南](https://voxcpm.readthedocs.io/zh-cn/latest/usage_guide.html)和
[最佳实践](https://voxcpm.readthedocs.io/zh-cn/latest/cookbook.html)。Skill
必须自包含上述运行所需规则，不要求运行中的 Agent 临时访问互联网。

## 4. 开发改动

| 文件 | 改动与边界 |
| --- | --- |
| `skills/voxcpm-tts/` | Git move 为 `skills/tts/`，不保留旧目录 |
| `skills/tts/SKILL.md` | 修改名称、metadata、env.d、模式选择、file ref/profile、原生标签、超限处理和 `publish_file` 工作流；删除 checkout + `skills.explicit` 与 `--env-file` 安装示例 |
| `skills/tts/scripts/botified-tts` | 只消费进程环境；`voice-create` 必填 `--filename` 并按 suffix 发送固定 `reference.<ext>`；不复制输入；其余命令和 HTTP 行为保持 |
| `tests/test_skill_helper.py` | 更新路径和环境注入，TDD 覆盖新的 filename/wire 行为，保留 helper 行为测试，删除 env-file parser 测试 |
| `README.md` | 写明 Core 版本、bash/host 依赖、executable bit、resolved Agent root、唯一安装配置、breaking CLI、升级和调用方式 |
| `docs/development-constraints.md` | 区分服务/companion env-file 与 Skill 的 env.d 客户端环境 |

测试只覆盖本项目拥有的行为：

- 现有命令、四种合成方式和 WAV/Ogg；
- 官方 11 个非语言标签保持原样通过 helper 和服务现有 segmenter；
- 参数、服务、MIME、音频和原子输出错误；
- helper 从环境读取 URL/key，且 `health` 在完全没有 key 时仍可成功；
- 认证命令缺 URL/key、URL 非 HTTP(S) 或 key 非法时不发请求；
- key 不进入 curl argv、stdout、stderr 或 curl 子进程环境。

现有 helper 测试层使用一个参数化/表驱动成功用例覆盖三行映射：

| `--file` / `--filename` 场景 | Multipart wire filename |
| --- | --- |
| 无扩展名路径；filename 包含中文、空格、逗号、分号、引号、反斜杠并以混合大小写 `.WaV` 结尾 | `reference.wav` |
| 普通 filename 以 `.flac` 结尾 | `reference.flac` |
| 普通 filename 以 `.mp3` 结尾 | `reference.mp3` |

每行只断言上传 payload 与原文件一致，以及 wire filename 匹配；不把格式、特殊字符、
大小写、profile mode 或服务解码扩成笛卡尔积。失败用例仍只覆盖缺少
`--filename`、无 suffix 和 `.ogg`，并确认请求未发送。

不测试 Botified 如何生成 manifest 或 `agent_path`，也不为用户确认拆分、
`publish_file` 或服务解码增加新的自动测试；它们是 Skill 工作流或既有 Botified/
服务边界。B2 只扩充现有 helper 测试，不新增服务、GPU、E2E 测试或测试层。

删除 env-file 缺失、重复 key、CRLF、引号和 shell 文本测试；这些格式现在由
Botified env.d 拥有。标签验证复用现有 `tests/test_segmenter.py` 对完整列表的
覆盖，并在现有 helper 路由用例中使用一个带标签的文本验证不被改写；不增加第二
套标签测试。开发迭代运行：

```bash
uv run pytest -q tests/test_skill_helper.py
```

完成前按仓库既有方式运行一次 `uv run pytest -q` 检查回归；这是现有测试的正常
使用，不增加新的测试层或 release gate。

README 应说明 Skill 在下一次 fresh provider request 重新发现，env.d 在下一次
Bash 启动重新加载，二者正常更新都不需要重启 Core。Skill 本体只保留 Agent
执行需要的短工作流，安装和迁移细节只放 README。

`docs/development-constraints.md` 的当前事实改为：服务和 companion 保持各自现有
env-file；Skill helper 只消费 Botified Bash 注入的 URL/key。Env.d 不配置 TTS
服务进程、Botified Provider、Gateway、TUI 或 channel plugin。

## 5. Operator 迁移说明

本轮开发只修改当前仓库。本节规定 README 应交付的 operator 操作，不授权开发
团队修改本机或远端已安装 Skill、Botified YAML、服务文件或 secret；任何实际
部署仍需要用户对精确目标和动作另行授权。

共同前置步骤：

1. 将 Botified Core 升级到 `v0.4.45+`，确认内置 `bash` 已启用；
2. 确认 Core host 有 Bash、curl、`python3` 和 GNU coreutils；无需安装 CUDA、
   Torch 或 FFmpeg；
3. 确认真实 resolved Agent root，并检查发现根中没有另一个 `name: tts`；
4. 安装完整的 `skills/tts` 并保留 helper executable bit；
5. 原子创建 `env.d/botified-tts.env`，只写 URL 和 key。

旧 Skill 位于 Agent root 时，删除安装目标中的 `skills/voxcpm-tts` 后发起新的
provider request，再运行一次新的 Bash 调用验证；不修改 YAML，不重启 Core。

旧 Skill 通过 `skills.explicit` 指向 checkout 时，在安装新 Skill 和 env 后，
由现有 supervisor 停止 Core，精确删除 YAML 中旧的
`voxcpm-tts/SKILL.md` entry，同时删除安装目标中可能存在的旧 Skill，再启动
Core；不删除 checkout，也不依赖不存在的通用热重载。启动后确认只发现 Agent
root 中的 `tts`。

只有在新路径验证成功，且确认旧 helper env 文件没有被服务或 companion 使用后，
operator 才能清理旧文件。迁移不保留新旧 Skill 并行、alias 或 symlink。

## 6. 验收标准

### 6.1 仓库与 Helper

- 仅存在 `skills/tts/SKILL.md`，其目录名、`name: tts`、description 和
  `when_to_use` 一致；
- 不存在旧 Skill、alias、兼容 wrapper 或第二个配置入口；
- README 明确 Core `v0.4.45+`、Botified `bash` enabled、Core host 的
  Bash/curl/`python3`/GNU coreutils 前置，并说明 Skill host 不需要 CUDA、Torch
  或 FFmpeg；
- `scripts/botified-tts` 保持 executable，且不再包含或接受 `--env-file`；
- `voice-create` 对所有调用要求 `--file` 和 `--filename`，不从 file basename
  推导；有效 suffix 大小写不敏感，wire filename 固定为
  `reference.wav|reference.flac|reference.mp3`，原始 filename 不进入 curl 或
  profile metadata，也不产生输入副本或清理流程；
- 同一 helper 测试层的一个参数化/表驱动成功用例覆盖：复杂的无扩展名路径与特殊
  字符 `.WaV` filename 映射为 `reference.wav`、简单 `.flac` 映射为
  `reference.flac`、简单 `.mp3` 映射为 `reference.mp3`；每行只验证 payload 与
  wire filename，不做笛卡尔积；缺 filename、无 suffix 和 `.ogg` 在请求前失败；
  不增加服务、GPU、E2E 测试或新测试层；
- 仅设置 URL/key 后，helper 可完成 health、音色管理和四种模式的 WAV/Ogg 合成；
- `health` 在 key 完全缺失时仍成功；认证命令缺 URL/key、URL 非 HTTP(S) 或 key
  非法时在请求前失败，错误不回显 key；
- key 不进入 helper/curl argv、stdout、stderr 或 curl 子进程环境；
- 原子输出、MIME 和 Ogg/Opus 验证继续有效；
- `uv run pytest -q tests/test_skill_helper.py` 通过，现有仓库测试无回归；
- README 和当前约束准确，服务、Docker、API、companion 与外部仓库无变化。

### 6.2 Botified 用户闭环

- Core 的全部发现根合计只加载一个 `name: tts`，下一次 fresh request 可显式
  调用 `$tts`；
- `description` 和 `when_to_use` 包含 TTS 与语音交付的正向触发，正文明确排除
  ASR 和已有音频处理；
- Skill 完整列出 11 个官方标签及其使用约束，说明多语言、方言、标点、Voice
  Design、style、参考音频和四种模式的选择规则；
- Skill 说明 available file ref 的 `agent_path` 和 `filename` 必须分别传给
  `--file` 与 `--filename`；unavailable 或缺少 `agent_path` 时请用户重传；创建
  后读取返回 JSON `id`，报告并作为后续 `--voice-id`；按名称查找时先 list，
  零匹配或重名不猜；controllable 不要求转写，faithful 要求精确逐字转写且不调用
  ASR；
- 默认声音允许可选 `--style` 但不承诺跨请求音色一致，需复用身份时使用 profile；
- Skill 不出现当前 helper 未实现的 VoxCPM2 参数或 flags；
- 新 Bash 中 helper 无 `--env-file` 即可使用 env.d 配置；
- 超过 8192 UTF-8 bytes 或收到 `input_too_large` 时，未经用户确认不截断或拆分；
  确认后按自然边界编号、复用已选 options、分别生成并发布，且不承诺跨文件连续性；
- 普通 WAV/Ogg 通过 `publish_file` 使用匹配 MIME 发布，不携带
  `audio_as_voice: true`；
- Ogg 语音消息通过 `publish_file` 使用 `audio/ogg` 和
  `audio_as_voice: true` 发布，返回调用方可取得的 published metadata；
- Skill 文档不指示 Agent 输出或传递 key；helper 自身不在参数、输出或错误中
  泄露 key。Env.d 的全局可见风险按 Botified 既有产品边界明确说明。

自然语言“发语音”可以做一次人工体验观察，但不作为完成条件，也不建立 LLM 路由
测试。

达到以上标准即完成本轮工作；发现问题时在上述唯一 owner 中原地修正，不增加
兼容路径或开发治理。
