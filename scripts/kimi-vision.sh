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

python3 -c "
import base64, json, urllib.request, sys
with open('$IMAGE', 'rb') as f:
    img = base64.b64encode(f.read()).decode()
payload = {
    'model': 'kimi-k2.6',
    'messages': [{'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{img}'}},
        {'type': 'text', 'text': '$PROMPT'}
    ]}],
    'max_tokens': 2000
}
req = urllib.request.Request(
    '$BASE_URL/chat/completions',
    data=json.dumps(payload).encode(),
    headers={'Authorization': 'Bearer $API_KEY', 'Content-Type': 'application/json'}
)
with urllib.request.urlopen(req, timeout=120) as resp:
    r = json.loads(resp.read())
    if 'error' in r:
        print(f'ERROR: {r[\"error\"]}', file=sys.stderr)
    else:
        print(r['choices'][0]['message']['content'])
"
