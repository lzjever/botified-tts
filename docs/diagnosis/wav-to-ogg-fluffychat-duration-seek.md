# WAV 转 Ogg 后 FluffyChat 时长与拖动问题

记录时间：2026-07-28

## 结论

- 当前 WAV 转 Ogg/Opus 的编码参数正确，转码耗时远低于 TTS 推理和
  Matrix 上传耗时，不构成性能瓶颈。
- 生成的 Ogg 文件时间戳连续、能够完整解码，也能快速跳转到指定位置；文件本身
  具备 seek 能力。
- 已检查的 Matrix 语音事件同时包含正确的 `info.duration`、
  `org.matrix.msc1767.audio.duration` 和 `org.matrix.msc3245.voice`。
- FluffyChat 在播放器尚未初始化时，拖动进度条只会触发下载和播放；开始播放后
  才会执行 seek。
- iOS/iPadOS 上存在一个潜在兼容问题：Matrix 事件使用
  `audio/ogg; codecs=opus`，而 FluffyChat 当前只在 MIME 严格等于
  `audio/ogg` 时执行 Ogg 到 CAF 的转换。若 iOS 上开始播放后仍不能拖动，应优先
  检查这一点。

## 转码参数

本次测试使用：

```bash
ffmpeg -nostdin -hide_banner -loglevel error \
  -i input.wav \
  -c:a libopus \
  -b:a 48k \
  -application voip \
  -vbr on \
  -f ogg output.ogg
```

这些参数适合 TTS 语音：

- Ogg 容器和 Opus 编码符合 Matrix 语音消息的常见格式。
- `48k` 对单声道语音足够。
- `application=voip` 偏重语音清晰度。
- `vbr=on` 允许编码器根据语音复杂度调整实际码率。
- libopus 默认帧长已经是 20 ms，不需要重复指定。

为了使未来不同来源的 WAV 也得到固定格式，可以显式增加：

```bash
-map 0:a:0 -ac 1 -ar 48000
```

当前 VoxCPM 输出本身已经是 48 kHz 单声道，因此增加这些参数不会改变当前结果，
也不会修复 FluffyChat 的拖动问题。

参考：[FFmpeg libopus 文档](https://ffmpeg.org/ffmpeg-codecs.html)。

## 转码耗时

在 Botified 所在主机上对实际生成的音频测试，输出写入 Ogg muxer，但不落地保留
额外测试文件。

| WAV 时长 | 多次转码耗时 | 平均耗时 |
|---|---:|---:|
| 7.36 秒 | 122、144、137、122、130 ms | 132 ms |
| 39.68 秒 | 435、340、307 ms | 361 ms |

短音频会受到 FFmpeg 进程启动时间影响。即便是约 40 秒音频，转码也不到半秒，
因此无需为此引入常驻转码服务或额外缓存。

## 时长差异

两个样本的探测结果：

| WAV 时长 | FFprobe 显示的 Ogg 时长 | 差异 |
|---:|---:|---:|
| 7.360000 s | 7.366500 s | 6.5 ms |
| 39.680000 s | 39.686500 s | 6.5 ms |

Ogg/Opus 流包含 312 个采样的 `pre-skip`：

```text
312 / 48000 Hz = 0.0065 s
```

RFC 7845 规定实际 PCM sample position 等于 granule position 减去
`pre-skip`。因此 Matrix 事件使用原始可播放音频时长 `7360 ms` 和
`39680 ms` 是正确的，FFprobe 多显示的 6.5 ms 不是音频被截断或容器损坏。

参考：[RFC 7845：Ogg Opus 封装](https://datatracker.ietf.org/doc/html/rfc7845)。

## Ogg 文件检查

检查结果：

- 48 kHz、单声道、Opus。
- 音频包时间戳连续。
- 完整解码无错误。
- 39.68 秒样本跳转到第 20 秒并解码 1 秒成功，耗时 32 ms。

可用以下命令重复检查：

```bash
ffprobe -v error \
  -show_entries \
  format=duration,size,bit_rate:stream=codec_name,sample_rate,channels,initial_padding,duration \
  -of json output.ogg

ffmpeg -nostdin -v error -i output.ogg -f null -

ffmpeg -nostdin -v error -ss 20 -i output.ogg -t 1 -f null -
```

上述结果说明服务生成的 Ogg 文件具备正常的随机跳转能力。

## Matrix 事件检查

实际发送的短音频事件包含：

```json
{
  "msgtype": "m.audio",
  "info": {
    "duration": 7360,
    "mimetype": "audio/ogg; codecs=opus",
    "size": 61175
  },
  "org.matrix.msc3245.voice": {},
  "org.matrix.msc1767.audio": {
    "duration": 7360
  }
}
```

较长音频对应的两个时长字段均为 `39680`。因此当前事件没有缺失时长元数据，
时长单位也正确地使用毫秒。

事件没有携带 waveform。FluffyChat 在 waveform 缺失时会显示普通 Slider，
这不应阻止 seek；缺少 waveform 只影响波形外观。

## FluffyChat 行为

FluffyChat 当前播放器的处理顺序是：

1. 首次点击或首次拖动时下载完整音频。
2. 将音频写入本地临时文件并初始化播放器。
3. 开始播放。
4. 播放器存在后，进度条拖动才调用 `audioPlayer.seek()`。

因此在开始播放前直接拖动，看起来会像“不能拖动”。应先点一次播放，等待播放器
初始化，再测试进度条。

参考：

- [FluffyChat 音频下载与播放器初始化](https://github.com/krille-chan/fluffychat/blob/main/lib/pages/chat/events/audio_player.dart#L154-L222)
- [FluffyChat Slider 与 seek 逻辑](https://github.com/krille-chan/fluffychat/blob/main/lib/pages/chat/events/audio_player.dart#L407-L430)

### iOS/iPadOS MIME 兼容性

FluffyChat 当前仅在下列条件成立时将 Ogg 转换为 CAF：

```dart
matrixFile.mimeType.toLowerCase() == 'audio/ogg'
```

当前 Matrix 事件中的 MIME 是：

```text
audio/ogg; codecs=opus
```

这个 MIME 合法且更明确，但不满足 FluffyChat 的严格字符串比较，可能导致 iOS
跳过兼容转换。该判断是基于源码作出的推断；是否正是用户遇到的问题，还需结合
FluffyChat 的平台和版本确认。

参考：[FluffyChat iOS Ogg 转换条件](https://github.com/krille-chan/fluffychat/blob/main/lib/pages/chat/events/audio_player.dart#L174-L189)。

## 排查顺序

遇到相同问题时按以下顺序检查即可：

1. 先开始播放，再拖动进度条。
2. 用 `ffprobe` 比较 WAV、Ogg 和 Matrix `info.duration`；几毫秒的
   Opus pre-skip 差异属于正常现象。
3. 用 `ffmpeg -ss` 验证文件本身是否能跳转。
4. 确认 FluffyChat 平台和版本。
5. 若为 iOS/iPadOS，重点检查事件 MIME 是否因为携带 `codecs=opus` 参数而跳过
   FluffyChat 的 CAF 转换。

当前没有理由调整 Opus 码率、帧长或 Ogg page 参数。
