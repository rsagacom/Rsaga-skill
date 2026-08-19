# 一致性引擎接入合同

本模块是 AI 影视创作工厂的视觉一致性控制平面，不是一个自研的人脸识别或
embedding 引擎。平台只负责请求、证据、阈值、版本和采用门禁；具体视觉判断
通过可替换 adapter 接入成熟视觉模型、ComfyUI 工作流或人工审片。

## 维度

- `face`：五官、脸型、发型和身份参考。
- `costume`：服装版本、颜色、材质和关键道具。
- `scene`：空间布局、时间天气、灯光和背景锚点。
- `pose`：动作、姿态和镜头连续性。

同一项目可以按真人近景、3DCG 中景、2D 动漫远景配置不同阈值；阈值不是全局
常量，也不能只按模型名称硬编码。

## 输出门禁

`ConsistencyFinding` 必须包含维度、状态、provider、model 和 `evidence_refs`。
`pass/warn/fail` 没有证据引用时，`ConsistencyAdapter` 自动降级为 `unknown`。
`unknown` 不能进入正式采用；必须转人工审片或接入真实视觉 provider。

本地 `UnknownConsistencyProvider` 永远只返回 `unknown`。它适合开发、合同测试
和离线预览，不代表模型已经看懂图片。

## 复用边界

优先复用以下成熟能力，不在平台内重复实现：

| 能力 | 复用底座 | 平台自有部分 |
|---|---|---|
| 图像/视频读取、抽帧、哈希 | FFmpeg/ffprobe | 证据引用和资产 ID |
| 视觉理解/相似度 | 已批准的视觉 API、ComfyUI 工作流或本地视觉模型 | adapter 输入输出合同 |
| 镜头切点候选 | PySceneDetect | 导演确认和镜头版本 |
| 人工审片 | 外部审片工具或平台审片 UI | Pass/Retake/Regenerate 决策和审计 |

新增视觉组件必须先经过国内源/官方源优先门禁，记录许可证、版本或 commit、
哈希和真实运行证据。文件存在、HTTP 200、工作流可提交均不等于视觉结论有效。
