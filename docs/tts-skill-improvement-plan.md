# TTS Skill 通用化与 env.d 迁移开发计划

> 状态：待开发
>
> 适用范围：当前 `botified-tts` 仓库的一次原地改进
>
> Botified 基线：`v0.4.45` 或更高版本

## 1. 问题与目标

当前 Skill 存在四个直接影响使用的问题：

1. `voxcpm-tts` 暴露底层模型，而用户需要的是稳定的 TTS 能力；
2. README 和 Skill 仍要求 `skills.explicit` 指向 repo checkout，没有使用最新版
   Botified 的 `<resolved-agents-dir>/skills`；
3. helper 主动清除继承的 API key，强制解析 `--env-file`，与 Botified
   `env.d` 的 Bash 注入机制冲突；
4. Skill 只报告生成文件的本地路径，没有使用 `publish_file` 完成普通文件或语音
   消息交付。

本轮交付后，Botified Agent 通过唯一的 `tts` Skill 使用现有 Botified TTS
服务。Skill 安装在 resolved Agent root，helper 只消费进程环境，面向调用方的
音频通过 Botified 已有的 `publish_file` 发布。

## 2. 范围

### 2.1 本轮完成

- 将 `skills/voxcpm-tts` 一次性改名为 `skills/tts`；
- 补齐面向 TTS、朗读、语音消息、音色克隆和 Voice Design 的 Skill metadata；
- 将 Skill 的唯一安装位置改为 `<resolved-agents-dir>/skills/tts`；
- 将 Skill 客户端的唯一配置入口改为
  `<resolved-agents-dir>/env.d/botified-tts.env`；
- 删除 helper 的 `--env-file` 和 env-file parser，直接读取
  `BOTIFIED_TTS_URL`、`BOTIFIED_TTS_API_KEY`；
- 保留普通合成、Voice Design、两种克隆、音色管理和 WAV/Ogg 输出；
- 在 Skill 工作流中加入普通音频发布和 Ogg 语音消息发布；
- 原地调整现有 helper 测试、README 和 `docs/development-constraints.md`。

### 2.2 明确不做

- 不修改当前仓库外的 Botified、Botified ASR、参考或上游仓库；
- 不修改 TTS 服务、HTTP/WebSocket API、VoxCPM2、Nano-vLLM、分段、音频编码、
  Docker 镜像或 companion；
- 不因本次 Skill 改动构建或发布 TTS Docker 镜像；
- 不增加多 TTS provider、backend/plugin 抽象、OpenAI 兼容或通用 TTS CLI；
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
description: Use the configured Botified TTS service to synthesize and publish WAV or Ogg/Opus speech, and to create, list, or delete trusted voice profiles.
when_to_use: TTS; text to speech; 文字转语音; 朗读文本; 生成、发送或回复语音消息; 音色克隆; Voice Design; 创建、查看或删除音色
---
```

正文保持简短，并规定：

1. 只处理已经适合朗读的纯文本，不解析 Markdown 或 SSML；
2. 根据意图选择普通合成、Voice Design、controllable clone 或 faithful clone；
3. 准备发布的文件直接生成到 runtime cwd 内的新相对 `.wav` 或 `.ogg` regular
   file，不覆盖已有文件，不使用绝对路径、`..`、cwd 外路径或 symlink；
4. 面向调用方的结果必须调用 `publish_file`，不能只报告服务端路径；
5. 普通附件使用匹配格式的 `audio/wav` 或 `audio/ogg`，省略
   `audio_as_voice`；
6. “发送/回复语音消息”默认生成 Ogg/Opus，并调用：

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

## 4. 开发改动

| 文件 | 改动与边界 |
| --- | --- |
| `skills/voxcpm-tts/` | Git move 为 `skills/tts/`，不保留旧目录 |
| `skills/tts/SKILL.md` | 修改名称、metadata、env.d 用法和 `publish_file` 工作流；删除 checkout + `skills.explicit` 与 `--env-file` 安装示例 |
| `skills/tts/scripts/botified-tts` | 只消费进程环境；其余命令和 HTTP 行为保持 |
| `tests/test_skill_helper.py` | 更新路径和环境注入，保留 helper 行为测试，删除 env-file parser 测试 |
| `README.md` | 写明 Botified 版本、resolved Agent root、唯一安装配置、升级和调用方式 |
| `docs/development-constraints.md` | 区分服务/companion env-file 与 Skill 的 env.d 客户端环境 |

测试只覆盖本项目拥有的行为：

- 现有命令、四种合成方式和 WAV/Ogg；
- 参数、服务、MIME、音频和原子输出错误；
- helper 从环境读取 URL/key，且 `health` 在完全没有 key 时仍可成功；
- 认证命令缺 URL/key、URL 非 HTTP(S) 或 key 非法时不发请求；
- key 不进入 curl argv、stdout、stderr 或 curl 子进程环境。

删除 env-file 缺失、重复 key、CRLF、引号和 shell 文本测试；这些格式现在由
Botified env.d 拥有。开发迭代运行：

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

1. 将 Botified Core 升级到 `v0.4.45+`；
2. 确认真实 resolved Agent root，并检查发现根中没有另一个 `name: tts`；
3. 安装完整的 `skills/tts`；
4. 原子创建 `env.d/botified-tts.env`，只写 URL 和 key。

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
- `scripts/botified-tts` 不再包含或接受 `--env-file`；
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
- 新 Bash 中 helper 无 `--env-file` 即可使用 env.d 配置；
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
