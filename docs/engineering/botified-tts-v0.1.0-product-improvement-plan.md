# Botified TTS v0.1.0 产品改进施工计划

> 状态：已收敛，可交付开发
> 适用范围：当前 `botified-tts` 仓库到 `v0.1.0` 的一次原地改进
> 基线引用：根目录 `AGENTS.md`、`docs/development-constraints.md`、
> `docs/engineering/botified-tts-product-development-plan.md`
> 上游基线：VoxCPM `616d3d3e630a9c96c2853250eef91b0f39dcd5fa`

本文仅是本轮施工计划，不是项目的全局真相。

## 1. 背景与问题

当前服务已有 HTTP、WebSocket、VoxCPM2、Nano、VoiceStore、Skill 和 companion。
本轮不重写核心，只处理影响交付的具体问题。

### 1.1 Companion

- WebSocket session 固定发送空 `{"type":"start"}`，实时朗读无法选择 profile、
  Voice Design、mode 或 style。
- provider replacement 创建新 session 时没有进程级 immutable options 可复用。
- 握手返回 `invalid_api_key`、`invalid_voice` 或 `invalid_request` 时，被误报为
  audio 不兼容。
- 错误应在创建 `aplay` 前被准确解释。

### 1.2 部署、模型与配置

- 当前 `deploy.sh + Compose + 本地 build` 要求普通用户 checkout 和构建。
- Dockerfile 位于 `deploy/`，仓库没有严格 `.dockerignore`。
- 模型下载只有 Hugging Face，model/revision 又可由用户任意组合。
- Docker 使用 env-file，Skill helper 和 companion 使用另一份 raw key file。
- 同一个 secret 因此存在两种格式和两份生命周期。

### 1.3 测试、版本与用户路径

- 现有旧名 GPU 脚本混合模型、HTTP/WS、语言样本、性能阈值和私有 child 拓扑。
- 同一协议行为在多层重复，真实 Nano 差异的 owner 不清楚。
- 根 package 仍是 `0.0.0`，companion 与仓库 tag 的关系未固定。
- 普通用户、Botified integrator、开发者和 Power user 的命令混在一起。

## 2. 目标与范围

### 2.1 本轮目标

- Companion 完整映射现有 WebSocket `start`，并正确呈现握手错误。
- Docker、Skill helper 和 companion 共用一份私有 `botified-tts.env`。
- 固定 ModelScope/Hugging Face 两份 spec，由用户显式选择 source。
- 普通用户只使用公开固定 image、env-file 和一个 `docker run`。
- 开发者保留 uv 路径，Power user 保留根 Dockerfile 构建路径。
- 测试收敛为 unit、service integration、companion integration 和一份真实 GPU
  integration。
- 用一个仓库 tag、一个公开 GHCR image 和一条最小 GitHub Release 发布。

### 2.2 明确不做

- 不修改 HTTP、WebSocket、VoiceStore、分段和 continuation 的公开协议。
- 不增加 OpenAI adapter、SSML、Markdown parser、播放确认或断线恢复。
- 不增加其他模型、推理后端插件、多 GPU、CPU fallback 或 worker restart。
- 删除 Compose、部署脚本、raw key 路径和旧 GPU 文件名，不保留兼容入口。
- 不增加自动 source 探测、跨源 fallback 或任意 model/revision 配置。
- 不增加 workflow、publish script、`latest`、release assets 或 changelog；
  不增加开发流程平台、留档材料、阶段汇报或准入机制。
- 不发布 PyPI，不把模型权重放进 image，不做 multi-arch、中国 registry 或自动
  同步。
- LICENSE 由用户或产品 owner 另行决定，不交给开发团队，不属于本轮交付。

### 2.3 写入边界

- 只修改当前 `botified-tts` 仓库。
- `../botified`、`../botified-asr`、上游仓库和 `.reference/` 只读。
- 集成代码只放在本仓库 `companions/botified/` 与 `skills/voxcpm-tts/`。

## 3. 四类用户路径

### 3.1 普通服务消费者

普通用户不 checkout、不运行 uv、不 build。唯一私有文件：

```text
BOTIFIED_TTS_API_KEY=replace_with_random_hex
BOTIFIED_TTS_MODEL_SOURCE=modelscope
BOTIFIED_TTS_LOG_LEVEL=INFO
```

文件权限为 `0600`；log level 可省略，source 可显式改为 `huggingface`。

唯一生产启动命令：

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

只用以下命令判断 ready 和查看失败：

```bash
docker inspect --format '{{.State.Health.Status}}' botified-tts
docker logs botified-tts
```

最终状态必须为 `healthy`，部署验证不额外合成。

### 3.2 Botified integrator

- Checkout 当前仓库，让 `skills.explicit` 直接指向
  `skills/voxcpm-tts/SKILL.md`。
- 不复制或 symlink Skill，不维护第二份 helper。
- Companion 使用独立 nested uv 环境和 Botified task preset。
- Companion 用 `--env-file` 读取同一 API key。
- Companion 只接受完整 `--tts-url ws://.../v1/speech/stream`。
- TLS 显式使用 `wss://.../v1/speech/stream`，不转换 URL。
- Companion README 和 task preset 只给两个最小示例：profile + mode + style，
  以及 design + style；两者使用绝对 checkout env 路径
  `/opt/botified-tts/botified-tts.env` 和完整 WebSocket endpoint。

### 3.3 开发者

完整本地测试唯一额外系统依赖是 `ffmpeg`：

```bash
command -v ffmpeg
uv sync --locked
uv run pytest -q
```

可选源码直跑：

```bash
BOTIFIED_TTS_DATA_DIR="$PWD/.data" \
  uv run --env-file ./botified-tts.env botified-tts
```

`.data/` 与 `botified-tts.env` 必须被 Git 忽略。源码直跑仍要求 CUDA。

### 3.4 Power user

```bash
docker build --platform linux/amd64 -t botified-tts:local .
```

构建后复用普通用户的 env-file、volume、GPU、port 和 `docker run`，只替换 image。

## 4. 唯一方案

### 4.1 Env-file 与 URL

- 唯一 secret 文件是 `botified-tts.env`。
- API key 必须匹配 `[A-Za-z0-9._~-]+`，不能为空且不加引号。
- 按第一个 `=` 后的字面值读取；必须恰好一条 `BOTIFIED_TTS_API_KEY=`。
- 不执行 quote、interpolation、command substitution 或 shell source。
- `Settings`、helper 和 companion 使用相同 key grammar。
- Helper 只从 `BOTIFIED_TTS_URL` 读取 HTTP base URL。
- Companion 只从 `--tts-url` 读取完整 WebSocket endpoint。
- README 用 `umask 077` 与 `openssl rand -hex 32` 创建文件，不新增生成脚本。

### 4.2 Companion

新增进程级参数：

```text
--voice-id <voice_id>
--design <description>
--mode controllable|faithful
--style <instruction>
```

- `--voice-id` 与 `--design` 二选一。
- 未提供选项时保持默认空 start options。
- Options 只构造一次，initial/replacement session 原样复用。
- Session 内不切换 voice、design、mode 或 style。
- 参数只映射服务 canonical schema；语义组合仍由服务校验。
- 握手先解析 JSON error，再校验 ready audio。
- 只有 PCM s16le、48 kHz、mono ready 后才创建 `aplay`。
- 异常、stdout 和 stderr 不输出 API key。

### 4.3 双模型来源

唯一公开选择必填且无默认、无 fallback：

```text
BOTIFIED_TTS_MODEL_SOURCE=modelscope|huggingface
```

| source | repo ID | immutable revision |
|---|---|---|
| `modelscope` | `OpenBMB/VoxCPM2` | `2e7c0dfff6646cef46c8bf106460a3dbce23a591` |
| `huggingface` | `openbmb/VoxCPM2` | `bffb3df5a29440629464e5e839f4d214c8714c3d` |

- Hugging Face 只用 `huggingface_hub.snapshot_download(...)`。
- ModelScope 只用 `modelscope_hub.HubApi().download_repo(...)`，参数含
  `repo_type="model"`。
- 两者都传固定 repo、revision 和计算出的 cache dir。
- Spec 只保存 repo ID/revision；cache 为
  `settings.data_dir / "model-cache" / settings.model_source`。
- CUDA preflight 早于 SDK import、下载和 Nano child 创建。
- 选中 source 失败返回 `model_load_failed`，不调用另一来源。
- Engine 把所选 SDK 返回的本地模型路径交给 Nano；app health 单一固定逻辑名
  `VoxCPM2`，runtime 不接收或传递模型展示参数。

### 4.4 Root Docker image

- `deploy/Dockerfile` 移到根 `Dockerfile`。
- 固定 Linux x86_64、CUDA runtime、uv、Python、FFmpeg 和 lock。
- Image 不包含权重；`/data/voices` 与 `/data/model-cache/<source>` 持久化。
- Image 固定 host `0.0.0.0`、port `8000`、device `0`、data dir `/data`。
- `HEALTHCHECK` 只访问 `/health`；ready 包含 CUDA、下载、load 和真实 warmup。
- 已 tracked 的 `.dockerignore` 修改为严格 allow-list，只允许 Dockerfile、
  `.dockerignore`、`pyproject.toml`、`uv.lock`、`src/` 父目录和 `src/**`。
- 删除 Compose 和 shell 部署入口。

### 4.5 测试所有权

Unit 只验证配置、固定 spec/cache、无 fallback、CUDA-first、canonical options、
分段、VoiceStore、PCM、continuation、资源上限和 env key grammar。

Service integration 使用 fake Nano，只验证 HTTP/WS、ready/done、cancel、慢客户端、
admission 和 runtime fatal 的服务行为。

Companion integration 使用 fake Botified frames/WebSocket/player，只验证 start
mapping、replacement、握手 error、barge-in、finish 后读事件和资源回收。

唯一真实 GPU 文件为 `tests/gpu_integration.py`，唯一 CLI 选择为
`--full-source modelscope|huggingface`。非 full source 先完成
create/load/wait/ordinary warmup 并 close；full source 在同一 engine 生命周期
覆盖：

1. CUDA、source、tokenizer、Nano create/load/wait 和 ordinary warmup。
2. 一次 Voice Design，spoken text 预留足够长度，生成的 clone reference 必须
   自然落在 VoiceStore `[3, 60]` 秒范围内，短则失败；禁止通过补静音、拼接额外
   生成结果或重试满足时长。fixture `prompt_text` 只保存精确 spoken text，排除
   description/style control prefix。
3. 同一 reference 的 controllable clone + style。
4. 同一 reference 与 exact spoken transcript 的 faithful clone。
5. 两段 continuation，文本包含一个原生非语言 tag，第二段使用第一段完整
   generated latents。
6. Outer stream `aclose()` 到达 child，随后同一 pool 完成短生成。
7. 仅当 Nano 提供稳定公开 owner 接口时，最后触发一次 child fatal；否则跳过，
   不读取或修改私有进程拓扑。

脚本不启动 HTTP/WS，不跑语言矩阵，不硬断言 RTF。两个 source 各完成 create 与
warmup；只有 full source 运行完整路径。

### 4.6 发布顺序

- 根 package 更新为 `0.1.0`；确认 companion `project.version` 已为 `0.1.0`，
  实际值正确时不制造 diff，companion 不独立发布。
- 在最终 clean commit 创建本地 `v0.1.0` tag。
- 同一可信、磁盘充足、有 CUDA 的 host 从该 tag clean checkout。
- Build 最终 local tag `ghcr.io/lzjever/botified-tts:v0.1.0`。
- 用唯一 `docker run` 启动 local image，等待 `healthy`。
- 先推送 Git tag，再推送已验证 image，并设置 GHCR package public。
- 最后创建只指向该 image 的简短 GitHub Release。

## 5. 四个施工阶段

| 阶段 | 改动 | 最小测试 | 完成结果 |
|---|---|---|---|
| 1. Botified 集成 | helper/companion 统一 env-file，删除 raw key；companion 增加 immutable start 与握手 error；同步 companion README、preset、Skill、根 README；Skill 只保留 `skills.explicit` | 默认 start；profile+mode+style replacement；design+style；握手 error 不创建 player；env 缺失/重复/空值/引号/非法字符/shell 文本；secret 不进入输出 | 同一 env-file 启动服务、helper、companion；实时朗读可固定音色或 Voice Design；错误 key/voice 不再误报 audio |
| 2. 双下载来源 | `Settings` 增加必填 source，删除公开 model/revision；engine 增加两份 spec 和单一 switch，把 SDK 本地路径传给 Nano；app health 单一固定 `VoxCPM2`，runtime 不传模型展示参数；固定 `modelscope-hub==0.1.8` 并更新根 lock | source 缺失/非法；两个 SDK 固定参数；失败无 fallback；CUDA 失败时 SDK/Nano 未 import、下载或创建；runtime 不接收或传递模型展示参数 | 两个来源显式可选且内容固定；用户不能任意组合 model/revision；Nano 收到本地路径，app health 返回固定逻辑名 |
| 3. 镜像与文档 | 移动根 Dockerfile；把 tracked `.dockerignore` 改为严格 allow-list；删除 Compose/`deploy.sh`；`.gitignore` 加 `/botified-tts.env`、`/.data/`；按四类用户重写 README | 根 pytest、companion tests/Ruff、根 `docker build`；context 不含 env-file、`.data/`、`.reference/` 或 companion | 普通用户只 pull/run，开发者只用 uv，Power user 只用根 Dockerfile；无第二套生产启动方式 |
| 4. GPU 与发布 | 旧 GPU 脚本改名并收敛为 `tests/gpu_integration.py`；删除重复协议、语言矩阵、硬 RTF、私有拓扑；根 package 更新 `0.1.0`，确认 companion 已是 `0.1.0` 且无需版本 diff；按固定顺序发布 | 两 source 各 create/warmup；一个 source 完成 design、两 clone、style、continuation、cancel 恢复；waveform 非空/finite、chunk 7680 samples；container `healthy` | 根 package 与 companion 均为 `0.1.0`，Git/GHCR 为 `v0.1.0`；image 可匿名拉取；Release 只指向 image |

## 6. 验收标准

- HTTP WAV、WebSocket 双向流、VoiceStore、分段、continuation、cancel、Voice
  Design、两类 clone、style 和原生标签保持可用。
- Companion 默认行为不变，支持 immutable profile/design/mode/style。
- Replacement、barge-in、finish 后 cancel 和资源回收保持有界。
- 握手错误保留 code/message，不创建 player，不泄露 key。
- Docker、helper、companion 共用 env-file，不存在 raw key 路径。
- Source 必填、无默认/无 fallback；spec/cache 各只有一个 owner。
- Engine 把所选 source 的本地模型路径传给 Nano；app health 单一固定
  `VoxCPM2`，runtime 不接收或传递模型展示参数。
- CUDA 不可用时在 SDK import、下载和 Nano child 前非零退出。
- 普通用户一个 `docker run` 达到 `healthy`；开发者可 sync/test/run；Power user
  可从根 Dockerfile build 并复用运行命令。
- 测试层各自拥有不同边界，单一 GPU integration 只覆盖真实 Nano 差异。
- 根 package 更新且 companion 现值确认后，两个 package、Git tag、GHCR tag
  一致；Release 无额外产物。
- 外部仓库只读，禁止范围和第二套实现均未进入仓库。

## 7. 文件变更清单

新增：本施工计划。

移动：`deploy/Dockerfile` → `Dockerfile`；tests 下现有旧名 GPU 脚本 →
`tests/gpu_integration.py`。

删除：`deploy/compose.yaml`；`scripts/deploy.sh`；raw key 参数/读取路径；旧部署、
旧 key、Skill copy/symlink 文档。

修改：`.dockerignore`、`.gitignore`、`README.md`、`pyproject.toml`、`uv.lock`、
`src/botified_tts/config.py`、`src/botified_tts/engine.py`、
`src/botified_tts/runtime.py`、`src/botified_tts/app.py`、`tests/test_config.py`、
`tests/test_engine.py`、`tests/test_runtime.py`、`tests/test_api.py`、
`tests/test_streaming.py`、`tests/test_skill_helper.py`、
`companions/botified/sidecar.py`、
`companions/botified/tests/test_sidecar.py`、`companions/botified/README.md`、
`skills/voxcpm-tts/SKILL.md`、`skills/voxcpm-tts/scripts/botified-tts`。

旧产品计划和项目约束仅作为基线引用，本轮施工不改写。
