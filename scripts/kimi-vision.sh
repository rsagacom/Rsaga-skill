#!/bin/bash
# Kimi K2.6 识图 — 备用视觉审核
# 用法: ./scripts/kimi-vision.sh <image-path> [prompt]
# 环境变量: KIMI_API_KEY

API_KEY="${KIMI_API_KEY:-}"
BASE_URL="https://api.moonshot.cn/v1"

IMAGE="$1"
PROMPT="${2:-描述这张图片的内容}"

if [ ! -f "$IMAGE" ]; then
  echo "Usage: $0 <image-path> [prompt]"
  exit 1
fi

if [ -z "$API_KEY" ]; then
  echo "错误：请设置环境变量 KIMI_API_KEY"
  exit 1
fi

# 使用 argv 传参而非字符串插值，避免 shell 注入
python3 - "$IMAGE" "$PROMPT" << 'PYEOF'
import base64, json, urllib.request, sys, os

image_path = sys.argv[1]
prompt = sys.argv[2]
api_key = os.environ.get("KIMI_API_KEY", "")

with open(image_path, "rb") as f:
    img_b64 = base64.b64encode(f.read()).decode()

payload = {
    "model": "kimi-k2.6",
    "messages": [{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
            {"type": "text", "text": prompt}
        ]
    }],
    "max_tokens": 2000
}

req = urllib.request.Request(
    "https://api.moonshot.cn/v1/chat/completions",
    data=json.dumps(payload).encode(),
    headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
)
with urllib.request.urlopen(req, timeout=120) as resp:
    r = json.loads(resp.read())
    if "error" in r:
        print(f"ERROR: {r['error']}", file=sys.stderr)
        sys.exit(1)
    else:
        print(r["choices"][0]["message"]["content"])
PYEOF
