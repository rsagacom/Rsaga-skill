# GOLDEN_SHOTS 回归库

`GOLDEN_SHOTS/manifest.json` 是项目已有真实成片的回归索引，不是新的生成器，也不是自动判断“艺术质量”的模型。

当前首批样本均来自 `test-runs/`，每条记录必须同时保留：

- 相对项目根的 MP4 路径；
- SHA-256；
- 历史人工审阅依据；
- 报告、工作流、ffprobe、抽帧或平台结果等证据引用；
- 分辨率、帧率、编码、时长容差；
- 模型、文本编码器、LoRA、Director/Motion Context 等生成元数据。

## 验证命令

```bash
cd /Volumes/AJW-Data/Projects/novel-to-comic-engine
.venv/bin/python scripts/verify_golden_shots.py
```

通过条件是每条样本都满足：文件存在、SHA-256 一致、ffprobe 元数据满足合同、`ffmpeg -v error -i ... -f null -` 完整解码返回 0。缺少 ffprobe/ffmpeg 或证据时返回 `unknown`，不能当作通过。

## 使用边界

- `accepted` 表示“历史人工审阅后可作为回归基线”，不表示所有景别、人物五官、口型和动作都生产级通过。
- 回归库用于检测模型/LoRA/工作流/封装链是否发生破坏；它不能替代视觉一致性 provider、人工审片或真实 GPU 重跑。
- 新样本必须先完成完整解码、哈希、抽帧和人工结论，再加入清单；不能因为 HTTP 200、文件存在或节点注册成功而加入。
- 任何模型、插件、工作流或媒体库引入仍遵守国内源优先和 Atlas `source-preflight` 门禁。

## 首批样本

1. 剪枝 INT8 + drbaph raw-key LoRA 的 5 秒 3DCG T2V 基线。
2. 剪枝 INT8 + drbaph raw-key LoRA 的 15 秒 Motion Context AV latent 接力基线。
3. 8B FP8 ClipProj MLP + Director 首段 / Motion Context 后续接力的 15 秒样本；该样本由用户确认画面较成功。

如果原始 MP4 被移动或重新封装，必须生成新 SHA 并增加新记录，不得静默覆盖旧证据。
