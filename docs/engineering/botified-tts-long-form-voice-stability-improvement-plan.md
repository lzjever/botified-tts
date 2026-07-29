# Botified TTS 长语音音色稳定性改进计划

> 状态：已收敛，可交付开发
> 目标版本：`v0.2.1`
> 适用范围：当前 `botified-tts` 仓库的一次原地质量改进
> 基线：根目录 `AGENTS.md`、`docs/development-constraints.md`
> 上游依据：VoxCPM 2 Usage Guide、OpenBMB/VoxCPM Issue #302 与 PR #313

本文是本轮独立施工计划，不是项目的全局真相。若本文与项目约束冲突，以
`AGENTS.md` 和 `docs/development-constraints.md` 为准。

## 1. 问题

使用 Voice Profile 合成超过约 30 秒的语音时，开头通常接近参考音色，随后可能
逐渐出现音色漂移、语气变化、发声变粗或噪声增加。

VoxCPM 官方文档确认长文本容易触发逐渐加速、buzzing 和其他不稳定行为，推荐把
文本拆成较短片段，并让每个 Voice Profile 片段重新使用同一份原始参考音频：

- https://voxcpm.readthedocs.io/en/latest/usage_guide.html#long-text
- https://voxcpm.readthedocs.io/en/latest/usage_guide.html#streaming

当前服务已经复用同一个 segmenter 切分 HTTP 完整文本和 WebSocket 增量文本，
但 `SpeechService` 会在每段完成后把本段生成 latent 和文本覆盖为下一段的
prompt。这样形成：

```text
原始参考 → 生成段 1 → 生成段 2 → 生成段 3 → ...
```

任何一段产生的音色、语速或发声误差都会被下一段继续继承。Faithful clone 还会
在第一段之后丢弃 Voice Profile 的原始 prompt latent 和精确 transcript。

OpenBMB/VoxCPM Issue #302 对 VoxCPM2 的单次长生成漂移给出了与源码相符但未经
官方确认的根因推断；PR #313 用固定首段 prompt 缓解单次长生成和逐段 chaining，
但仍未合并，且作者确认它不能消除每个片段内部的漂移：

- https://github.com/OpenBMB/VoxCPM/issues/302
- https://github.com/OpenBMB/VoxCPM/pull/313

## 2. 目标

- 长语音不再因跨段滚动 continuation 持续累积模型生成误差。
- Controllable clone 的每一段都重新使用原始 reference，并保持请求级 style。
- Faithful clone 的每一段都重新使用原始 reference、原始 prompt 和精确
  transcript。
- 没有原始声音参考的 Voice Design 和默认声音使用请求内固定首段，尽力减少逐段
  变化；默认声音跨请求仍可能随机。
- 默认保留较自然的分段跨度，同时提供一个固定 short profile，供更重视缩短单次
  生成跨度的部署选择。
- HTTP 非流式与 WebSocket 双向流继续共用同一个 `SpeechService` 和 segmenter。
- 不改变 HTTP/WS API、Voice Profile 数据、Docker 构建、Skill 或 companion
  用法。

## 3. 范围边界

### 3.1 本轮包含

- 原地替换 `SpeechService` 的跨段锚定方式。
- 比较 100/160 与 55/80 两个固定分段候选，保留 natural 默认并提供 short
  启动选项；两者都避免在有合适空白时从英文单词中间硬切。
- 修改直接拥有上述行为的最小单元测试。
- 更新现有真实 GPU integration 中的跨段用例。
- 更新开发约束和 README 中与跨段 continuation 不再相符的当前事实。

### 3.2 本轮不包含

- 不修改 VoxCPM2、Nano-vLLM-VoxCPM 或 `.reference/**`。
- 不加入 Issue #302 提出的 decoder latent 混合或 `voice_anchor_strength`。
- 不直接合入、复制或依赖 PR #313。
- 不新增 long-form endpoint、请求字段、mode、strategy、热更新或任意数字
  segment 配置；只增加一个服务启动级二选一 profile。
- 不公开 CFG、temperature、inference steps 或 seed。
- 不增加自动说话人相似度服务、质量评分平台、测试矩阵或开发治理产物。
- 不增加 SSML、Markdown parser、语言检测器或第三方 NLP 分句依赖。
- 不增加跨段固定静音、crossfade、重试、缓存或后处理；实际验证发现独立边界
  问题时再就地处理对应问题。
- 不修改 HTTP WAV/Ogg、WebSocket PCM、VoiceStore、下载来源、CUDA preflight、
  Docker、Skill、companion 或仓库外项目。
- 不承诺 VoxCPM2 对任意长度语音完全无漂移；本轮只消除服务自身的累积放大，并
  按官方建议限制每次生成跨度。

## 4. 唯一跨段规则

一次合成请求只允许使用一个不可变锚点。生成完成的片段不得滚动成为下一片段的
新锚点；不同合成方式只决定该锚点来自原始 Voice Profile 还是请求第一段。

| 合成方式 | 固定 reference | 固定 prompt | control/style |
|---|---|---|---|
| Controllable clone | Voice Profile 原始 reference latent | 无 | 每段都加同一请求级 control |
| Faithful clone | Voice Profile 原始 reference latent | Voice Profile 原始 prompt latent 与精确 transcript | 不允许 |
| Voice Design | 无 | 第一段生成 latent 与注入 control 前的原始 segment | 只用于第一段，后续由固定首段保持声音 |
| 默认声音 | 无 | 第一段生成 latent 与注入 control 前的原始 segment | 若有 style，只用于第一段 |

### 4.1 Controllable clone

每个片段独立调用 Nano：

```text
target_text = (control) + 当前片段
prompt_latents = None
prompt_text = ""
ref_audio_latents = 原始 reference latent
```

`control` 是现有 style 清理和控制指令逻辑得到的请求级指令。因为各片段不再
继承上一段，指令必须应用到每一段。没有 control 时不添加空括号。

### 4.2 Faithful clone

请求开始时读取一次 Voice Profile snapshot。因为 Nano 的 reference 与 prompt
采用不同 latent role 和 padding 方式，必须分别从缓存读取或分别编码一次，不能
用同一份 latent 兼任两个角色。随后每段复用这两份不可变值：

```text
target_text = 当前片段
prompt_latents = 原始 prompt latent
prompt_text = Voice Profile 的精确 transcript
ref_audio_latents = 原始 reference latent
```

任何片段的 `GenerationCompletion.generated_latents` 都不得覆盖原始 prompt。

### 4.3 Voice Design 与默认声音

这两种方式没有可重复注入的真人 reference，只提供请求内 best-effort 连续性，
不承诺达到 Voice Profile 的音色稳定性。第一段仍按现有方式生成；第一段完成后
保存：

```text
fixed_prompt_latents = 第一段 generated_latents
fixed_prompt_text = 第一段原始 segment 文本
```

后续所有片段始终复用这份固定 prompt，不再更新。`fixed_prompt_text` 不包含
注入前不存在的 `(control)` 前缀；文本中的 VoxCPM 官方非语言标签保持原样。不
通过分析生成音频反推 transcript。

Nano 已支持直接复用 generated latent，不写临时 WAV，也不重复 encode。

### 4.4 生命周期

- 固定锚点只存在于当前 `SpeechService.synthesize()` 调用中。
- 不写 VoiceStore，不跨请求缓存，不增加共享状态。
- 保持片段串行生成、现有 PCM chunk 输出、取消时关闭当前 Nano stream。
- `GenerationCompletion` 仍用于确认片段完整结束；只有无原始 reference 的第一段
  会保存其 latent。
- 第一段在 completion 前被取消或失败时不得提交固定 prompt，并且不得启动下一
  片段；继续由现有 consumer-close 和错误路径关闭当前 Nano stream。
- WebSocket 在第一个可提交 segment 到达后继续生成和发送音频，不等待 finish 或
  完整长文本；append、flush 和后续 segment 不得重置请求锚点，也不重新生成已经
  发送的音频。

## 5. 分段收敛

继续使用唯一的 `Segmenter`，不增加 long-form 专用分段器。

最终产品决定保留两个固定启动 profile：

| profile | 目标长度 | 硬上限 | 用途 |
|---|---:|---:|---|
| `natural` | 100 | 160 | 默认；减少分段边界，优先自然度 |
| `short` | 55 | 80 | 可选；缩短单次生成跨度，但边界更频繁 |

唯一服务配置为
`BOTIFIED_TTS_SEGMENT_PROFILE=natural|short`。缺省为 `natural`；非法值在启动时
以 `invalid_configuration` fail-fast。配置只在启动时读取，HTTP 与 WebSocket
共用同一值，不进入请求 schema、不支持热更新，也不接受任意数字阈值。ready
日志记录最终 profile，`/health` schema 不变。

其余原则：

1. 优先在 `。！？!?` 和可确认的英文句点结束处分段。
2. 达到目标长度时，优先使用逗号、分号、冒号或空白。
3. 达到硬上限时，优先回退到最近的空白，避免切断英文单词。
4. 找不到自然边界时才在硬上限切分，保证内存和延迟有界。
5. 保留小数、跨 append 的尾随数字句点和官方非语言标签保护。
6. 保持输入文本逐字符守恒，不增加文本归一化或改写。
7. 保持现有 0.8 秒增量输入 deadline、flush、finish 和短回复行为。

官方只建议使用短句，没有规定字符阈值。100/160 与 55/80 都是本项目固定选择，
不是 VoxCPM2 官方默认值。A/B 讨论最终选择 100/160 作为 Candidate A
`natural` 默认，以避免过多边界影响自然度；55/80 作为 `short` 保留给更重视
缩短生成跨度的部署。两者使用同一套自然边界和流式输入语义。

## 6. 实现位置

| 文件 | 改动 |
|---|---|
| `src/botified_tts/speech.py` | 保存不可变原始锚点；按现有 voice/mode 选择唯一固定锚点；删除滚动 prompt 覆盖；对每段 controllable clone 重复 control |
| `src/botified_tts/segmenter.py` | 唯一保存 natural 100/160 与 short 55/80 映射；硬切前优先英文单词边界 |
| `src/botified_tts/config.py` | 启动时解析并校验唯一 segment profile env；缺省 natural |
| `src/botified_tts/runtime.py`、`app.py`、`streaming.py` | 把同一 profile 传给 HTTP/WS；ready 日志记录最终值 |
| `tests/test_speech.py` | 证明三类固定锚点行为和 control 文本语义 |
| `tests/test_segmenter.py` | 证明两个固定 profile、文本守恒和英文单词边界 |
| `tests/test_config.py`、`test_api.py`、`test_runtime.py` | 证明 env fail-fast、HTTP/WS 共用值和 ready 日志 |
| `tests/gpu_integration.py` | 用现有 full-source 路径运行真实多段固定锚定，不新增 GPU 文件或矩阵 |
| `docs/development-constraints.md` | 把“跨段 continuation”更新为固定跨段锚定的当前产品边界 |
| `README.md` | 说明可选 profile、short 的边界代价和固定镜像版本，不展开声音模式内部策略 |
| `pyproject.toml`、`uv.lock` | 根包版本更新到 `0.2.1`，不改变依赖 |

`schemas.py`、`engine.py`、`audio.py`、VoiceStore、Skill、companion 和 Docker
不改。

## 7. 最小测试

### 7.1 SpeechService

在现有 `tests/test_speech.py` 中替换滚动 continuation 断言，不建立第二套测试
文件：

- Controllable clone 连续三个片段都使用同一 reference、空 prompt，并对每段
  应用同一 control。
- Faithful clone 连续三个片段都使用同一 reference、同一原始 prompt latent 和
  同一精确 transcript。
- 使用同一参数化 owner 分别覆盖 Voice Design 的 description/style 和默认声音
  的 style：第一段没有 prompt，后续片段都使用第一段固定 latent，第三段不得
  使用第二段 latent。
- 固定 prompt 文本排除 `(control)`，保留实际目标文本。
- Faithful 的 exact transcript 每段保持原字符串；Faithful + style 继续由
  `schemas.py` 现有拒绝测试拥有，不在 SpeechService 或 WebSocket 重复。
- 复用现有 stream close、summary 和错误测试；不为新策略复制取消、HTTP 或
  WebSocket 测试。

### 7.2 Segmenter

在现有 `tests/test_segmenter.py` 中：

- 证明默认 natural 使用目标 100、硬上限 160，short 使用目标 55、硬上限 80。
- 增加一个超过硬上限的英文句子，证明有可用空白时不从单词中间切断。
- 保留随机 append、文本守恒、小数和官方标签测试。
- 不增加语言、标点和缩写组合矩阵，不测试正则内部步骤。
- 在现有配置、runtime 与 API owner 中分别证明 env 缺省/非法值、ready 日志，
  以及同一个 short 设置同时到达 HTTP 与 WebSocket；不复制协议矩阵。

### 7.3 真实 GPU

现有 `tests/gpu_integration.py` 继续是唯一真实模型验证入口：

- 把现有 Voice Design reference 的朗读文本拆成三个显式片段，覆盖无 reference
  固定首段的真实 Nano 路径。
- 把 controllable clone 改为三个片段，覆盖原始 reference 的真实多段路径。
- 把 faithful clone 改为至少两个片段，覆盖原始 reference/prompt 两种 latent
  role 的真实重复使用。
- 删除被上述 Voice Design 三段路径取代的独立 default 两段 continuation；保留
  取消恢复，不重复 default、HTTP/WS、Ogg 或下载测试。Voice Design 与 default
  共用相同的无 reference Nano prompt 路径，二者公开参数构造由单元测试拥有。
- 只断言真实 Nano 可以完成生成并返回有效 PCM，不用自动指标冒充音色判断。

开发前先用已发布 `v0.2.0` 保存基线。阶段一完成后保留现有 100/160 分段常量，
用同一 GPU、同一 Voice Profile 和同一段 60–90 秒中文对比固定锚点；阶段二再用
同一输入对比 55/80。这样分别确认锚点和分段变化，不把两个质量变量混在一次判断
中。由于 55/80 会增加边界，最终产品选择 100/160 作为 natural 默认，并把
55/80 保留为显式 short 选项；选择 short 时接受更频繁、可能较不自然的边界。

最终使用同一份 60–90 秒文本分别完成 controllable 和 faithful clone，直接听
开头、中段、结尾及分段边界。Voice Design/default 只提供 best-effort 请求内
连续性，复用真实三段 GPU 路径，不扩成长音频试听矩阵。先用 FFprobe 确认文件
完整、48 kHz、单声道和时长合理。样本放在已忽略的 `.data/`，不提交音频、
评分、报告或额外工具。

修正后不得继续呈现随段数明显加重的音色漂移，也不得出现明显 buzzing、加速、
重复、截断、异常长静音或不可接受的边界跳变。若单个短段尾部仍有 VoxCPM2 固有
漂移，只在交付结论中说明上游限制，不创建报告，也不在本轮加入推理内核补丁。

完成代码后运行：

```bash
uv run pytest -q
```

只有需要确认真实 Nano 路径时运行现有 GPU integration，不在普通测试中重复。

## 8. 施工顺序

### 阶段一：固定锚点

- 在 `SpeechService` 中分离不可变原始锚点和无参考模式的固定首段 prompt。
- 删除每段完成后滚动覆盖 prompt 的实现。
- 更新 `tests/test_speech.py`，先证明各模式只有一种锚定方式。

阶段结果：服务不再跨段累积模型生成误差，公开协议不变。

### 阶段二：确定分段 profile

- 使用阶段一相同输入先保存固定锚点 + 100/160 的结果。
- 使用同一 Segmenter 增加固定 55/80 候选。
- 在硬上限回退时优先英文空白。
- 更新现有 segmenter 测试。
- 使用相同输入生成 55/80 结果，确认段内稳定性改善且边界、停顿和总时长没有
  明显倒退。
- 最终选择 100/160 为 natural 默认，并仅用启动级 env 提供 55/80 short；HTTP
  与 WebSocket 共用，不增加数字配置。

阶段结果：HTTP 完整文本和 WebSocket 增量文本共用一个启动时固定的分段
profile；默认优先自然边界，需要时可显式选择更短跨度。

### 阶段三：真实模型、当前文档与发布

- 更新并运行现有 GPU integration 的多段路径。
- 使用同一文本分别完成 controllable 和 faithful 的 60–90 秒实际试听。
- 只更新 README 和开发约束中的当前事实。
- 删除被新规则替代的 continuation 描述，不保留旧锚定策略或 fallback。
- 根包和 lock 更新到 `0.2.1`。
- README 说明可选 short profile 及其边界代价；发布镜像缺省使用 natural。
- 从同一 clean commit 创建并推送 `v0.2.1` tag，按现有唯一方式构建和发布
  `ghcr.io/lzjever/botified-tts:v0.2.1`，匿名拉取后使用现有 Docker 启动方式
  达到 `healthy`，最后创建只指向固定镜像的简短 GitHub Release。
- 不创建 `latest`、额外 registry、release asset、workflow 或发布脚本。

阶段结果：真实 Nano 路径可用，文档只描述当前唯一行为，普通用户可以部署固定
修复版本。

## 9. 验收标准

- 任一模式都不得滚动采用最近完成片段的 generated latent；无 reference 模式只
  允许固定使用第一段锚点。
- Controllable clone 每段使用同一原始 reference；style 在每段生效。
- Faithful clone 每段使用同一原始 reference、prompt latent 和精确 transcript。
- Voice Design/默认声音后续片段只使用固定第一段，不滚动更新。
- Voice Design/default 的固定首段只提供请求内 best effort；默认声音跨请求仍可
  随机，不把它验收为 Voice Profile 等级的一致性。
- 缺省 natural 的目标长度为 100、硬上限为 160；显式 short 为 55/80。两者都
  优先自然边界，英文有合适空白时不从单词中间硬切，所有输入文本完整且顺序不变。
- `BOTIFIED_TTS_SEGMENT_PROFILE` 只接受 `natural|short`，缺省 natural，非法值
  启动 fail-fast；HTTP/WS 共用最终值，ready 日志可见，health schema 不变。
- HTTP WAV/Ogg 和 WebSocket PCM 的 endpoint、schema、MIME、帧格式、错误语义
  和取消行为不变。
- WebSocket 在第一个可提交 segment 后即可持续出音，不等待 finish/全文；
  append、flush 不重置锚点，不重新生成已发送音频。
- Voice Profile、VoiceStore、Skill、companion、Docker、模型 revision、下载
  来源和 CUDA preflight 不变。
- 普通测试通过；现有 GPU integration 完成真实多段生成。
- 60–90 秒实际 clone 不再呈现由服务滚动 continuation 导致的逐段累积恶化，
  分段边界没有明显到不可接受的音色跳变。
- 没有新增请求级或任意数字配置、第二套 segmenter/pipeline、上游 fork、质量
  平台、报告或治理文件。
- 根包、Git tag、公开 GHCR 固定镜像和 GitHub Release 均为 `v0.2.1`，旧
  `v0.2.0` tag 和镜像保持不变。
- 只修改当前仓库；`.reference/**` 和所有仓库外项目保持只读。
