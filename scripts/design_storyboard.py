#!/usr/bin/env python3
"""分镜设计脚本：用 Kimi K2.7 读原文，产出带镜头连贯性的分镜设计。
不是1句1格独立画面，而是按场景组织分镜序列，每格标注镜头关系。
key 从 VOLC_API_KEY 环境变量读，不硬编码。
"""
import json, os, sys, urllib.request

api_key = os.environ.get("VOLC_API_KEY")
if not api_key:
    sys.exit("ERROR: 需设 VOLC_API_KEY")
API_URL = "https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions"

chapter_path = sys.argv[1]
out_path = sys.argv[2] if len(sys.argv) > 2 else "storyboard_design.json"

novel = open(chapter_path, encoding="utf-8").read()

SYSTEM = """你是资深漫画分镜师，擅长把小说转成有电影感的连贯分镜。
核心要求：
1. 按场景组织分镜（同一场景的连续动作组成一个分镜序列），不是1句1格独立画面。
2. 每格必须标注与上一格的镜头关系，保证翻页时有电影般流动感：
   - 景别递进：远→中→近→特写，或反向，不要每格都重新构图
   - 角度变化：正面/侧面/俯仰/背影，对话用正反打
   - 构图承接：上一格出现的物件/光影下一格要延续
   - 视觉节奏：高潮用大特写/留白，过渡用中景
3. 同一场景内人物位置、光线方向、环境元素要保持一致（连续性）。
4. 画风固定：东亚黑白漫画/2D ink/manhua，禁真人照片感。
5. 超自然角色无五官（只有发光眼窝/剪影/烟雾）。
6. prompt 不要求生图模型画可读文字，文字交给排版。

输出严格 JSON（只输出JSON，不要任何其他文字）：
{
  "scenes": [
    {
      "scene_id": 1,
      "scene_desc": "场景概述（地点/时间/氛围）",
      "panels": [
        {
          "panel_id": "S1P1",
          "shot": "景别(远/中/近/特写)",
          "angle": "角度(正面/侧面/俯/仰/背影)",
          "link_to_prev": "与上一格的镜头关系（首格写'开场'）",
          "prompt": "画面描述（中文，具体可见元素，含画风约束，120字内）",
          "bubble": "配文（旁白/对白，简短）",
          "audit_type": "character/scene/supernatural/abstract"
        }
      ]
    }
  ]
}"""

payload = {
    "model": "kimi-k2.7-code",
    "max_tokens": 8000,
    "messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"为这篇小说章节设计连贯分镜：\n\n{novel}"},
    ],
}
proxies = {}
for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
    if os.environ.get(k):
        proxies["https"] = os.environ[k]; break
opener = urllib.request.build_opener(
    urllib.request.ProxyHandler(proxies) if proxies else urllib.request.ProxyHandler({}))

req = urllib.request.Request(API_URL, data=json.dumps(payload).encode(),
    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
try:
    with opener.open(req, timeout=600) as resp:
        r = json.loads(resp.read())
    content = r["choices"][0]["message"].get("content") or ""
    # 提取 JSON
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        data = json.loads(content[start:end+1])
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        total = sum(len(s["panels"]) for s in data.get("scenes", []))
        print(f"✓ 分镜设计完成: {len(data.get('scenes',[]))} 场景, {total} 格")
        print(f"  保存: {out_path}")
    else:
        print("ERROR: 未找到JSON，原始输出前500字：")
        print(content[:500])
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}: {e.read().decode()[:500]}")
except Exception as e:
    print(f"ERROR: {e}")
