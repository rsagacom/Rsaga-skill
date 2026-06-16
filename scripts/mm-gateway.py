#!/usr/bin/env python3
"""多模态本地网关 — CC 通过 localhost:11939 调用，key 不经过 CC 命令"""
import json, base64, urllib.request, os, re, sys, io, signal
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

PORT = 11939
SCRIPT_DIR = Path(__file__).parent

# ─── 加载 keys ──────────────────────────────────────────────
def load_key(filename, env_var, prefix=""):
    """从文件 fallback 加载 key"""
    key = os.environ.get(env_var, "")
    if key:
        return key
    f = SCRIPT_DIR / filename
    if f.exists():
        with open(f) as fh:
            m = re.search(r'API_KEY:-\{?([A-Za-z0-9-]{40,80})\}?', fh.read())
            if m:
                key = m.group(1).strip('{}')
    return key

STEP_KEY = load_key("step-image-gen.sh", "STEP_API_KEY")
KIMI_KEY = load_key("kimi-vision.sh", "KIMI_API_KEY")

if not STEP_KEY:
    print("FATAL: 未找到 STEP_API_KEY", file=sys.stderr)
    sys.exit(1)

print(f"🔑 STEP_KEY: {len(STEP_KEY)} chars, KIMI_KEY: {'✓' if KIMI_KEY else '✗'}")

# ─── API 调用封装 ──────────────────────────────────────────
def step_vision(img_path, prompt, max_tokens=400):
    """Step 3.7 Flash 识图"""
    with open(os.path.expanduser(img_path), 'rb') as f:
        img_b64 = base64.b64encode(f.read()).decode()
    payload = {
        'model': 'step-1o-turbo-vision',
        'messages': [{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{img_b64}'}},
            {'type': 'text', 'text': prompt}
        ]}],
        'max_tokens': max_tokens
    }
    req = urllib.request.Request(
        'https://api.stepfun.com/v1/chat/completions',
        data=json.dumps(payload).encode(),
        headers={'Authorization': f'Bearer {STEP_KEY}', 'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        r = json.loads(resp.read())
    if 'error' in r:
        return {'error': r['error']}
    return {'content': r['choices'][0]['message']['content'], 'usage': r.get('usage', {})}

def kimi_vision(img_path, prompt, max_tokens=2000):
    """Kimi K2.6 识图"""
    if not KIMI_KEY:
        return {'error': 'KIMI_API_KEY 未配置'}
    with open(os.path.expanduser(img_path), 'rb') as f:
        img_b64 = base64.b64encode(f.read()).decode()
    payload = {
        'model': 'kimi-k2.7-code',
        'messages': [{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{img_b64}'}},
            {'type': 'text', 'text': prompt}
        ]}],
        'max_tokens': max_tokens
    }
    req = urllib.request.Request(
        'https://api.moonshot.cn/v1/chat/completions',
        data=json.dumps(payload).encode(),
        headers={'Authorization': f'Bearer {KIMI_KEY}', 'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        r = json.loads(resp.read())
    if 'error' in r:
        return {'error': r['error']}
    return {'content': r['choices'][0]['message']['content'], 'usage': r.get('usage', {})}

def step_generate(prompt, size="1024x1024", style=None, seed=None, cfg_scale=1.0, steps=8):
    """Step 文生图"""
    # 注入风格前缀
    style_prefixes = {
        "ui-wireframe": "UI wireframe sketch, black and white, clean lines, placeholder text, simple geometric shapes, no color, minimal detail, professional UX layout.",
        "ui-mockup": "High fidelity UI mockup, modern flat design, clean typography, subtle shadows, professional color scheme, realistic app screen, 8pt grid system, white background with centered UI.",
        "ui-sketch": "Hand-drawn style UI sketch, pencil or pen strokes, rough but readable, creative app concept, natural paper-like texture background, annotations in margins.",
        "mobile-app": "Beautiful mobile app screen, iOS style, rounded corners, modern design language, clean interface, soft lighting on device frame, SF-style typography.",
        "desktop-web": "Desktop web application interface, browser chrome visible, modern SaaS design, dashboard style, data visualization, clean professional layout.",
        "dark-ui": "Dark mode UI design, dark gray #1a1a2e background, neon accent colors, glassmorphism panels, glowing elements, futuristic tech aesthetic.",
        "icon": "App icon design, minimalist, centered on plain background, clean vector style, single focused subject, high contrast, recognizable at small sizes.",
        "logo": "Logo design, minimalist, centered, vector graphics style, bold and memorable, white or transparent background, suitable for tech brand.",
        "illustration": "Digital illustration, modern flat vector art style, vibrant colors, clean composition, suitable for web or app onboarding.",
        "diagram": "Technical architecture diagram, clean flow chart, boxes and arrows, labeled components, white background, professional documentation style.",
    }
    if style and style in style_prefixes:
        prompt = style_prefixes[style] + " " + prompt

    payload = {
        'model': 'step-image-edit-2',
        'prompt': prompt,
        'n': 1,
        'size': size,
        'response_format': 'b64_json',
        'cfg_scale': cfg_scale,
        'steps': steps,
        'text_mode': True,
    }
    if seed:
        payload['seed'] = int(seed)

    req = urllib.request.Request(
        'https://api.stepfun.com/v1/images/generations',
        data=json.dumps(payload).encode(),
        headers={'Authorization': f'Bearer {STEP_KEY}', 'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        r = json.loads(resp.read())
    if 'error' in r:
        return {'error': r['error']}

    # 解码 b64_json
    images = []
    for img in r.get('data', []):
        b64_data = img.get('b64_json', '')
        if b64_data:
            images.append(base64.b64decode(b64_data))
    return {'images': len(images), 'size': size}

def step_edit(img_path, prompt, cfg_scale=1.0, steps=8, seed=None):
    """Step 图像编辑"""
    img_path = os.path.expanduser(img_path)
    ext = img_path.rsplit('.', 1)[-1].lower() if '.' in img_path else 'png'
    mime_map = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'webp': 'image/webp'}
    mime_type = mime_map.get(ext, 'image/png')

    with open(img_path, 'rb') as f:
        img_data = f.read()

    boundary = '----MMGatewayEditBoundary'
    body = b''
    fields = {
        'model': 'step-image-edit-2',
        'prompt': prompt,
        'response_format': 'b64_json',
        'cfg_scale': str(cfg_scale),
        'steps': str(steps),
        'text_mode': 'true',
    }
    if seed:
        fields['seed'] = str(seed)

    for key, val in fields.items():
        body += f'--{boundary}\r\n'.encode()
        body += f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
        body += f'{val}\r\n'.encode()

    body += f'--{boundary}\r\n'.encode()
    body += f'Content-Disposition: form-data; name="image"; filename="input.{ext}"\r\n'.encode()
    body += f'Content-Type: {mime_type}\r\n\r\n'.encode()
    body += img_data + b'\r\n'
    body += f'--{boundary}--\r\n'.encode()

    req = urllib.request.Request(
        'https://api.stepfun.com/v1/images/edits',
        data=body,
        headers={
            'Authorization': f'Bearer {STEP_KEY}',
            'Content-Type': f'multipart/form-data; boundary={boundary}',
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        r = json.loads(resp.read())
    if 'error' in r:
        return {'error': r['error']}

    images = []
    for img in r.get('data', []):
        b64_data = img.get('b64_json', '')
        if b64_data:
            images.append(base64.b64decode(b64_data))
    return {'images': len(images)}

# ─── HTTP Handler ──────────────────────────────────────────
class GatewayHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"  {args[0]}", flush=True)

    def _send_json(self, code, data):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_GET(self):
        if self.path == '/health':
            self._send_json(200, {
                'status': 'ok',
                'step_key': bool(STEP_KEY),
                'kimi_key': bool(KIMI_KEY),
            })
        else:
            self._send_json(404, {'error': 'not found'})

    def do_POST(self):
        body = self._read_body()

        # ─── Chat (Kimi 纯文本) ───────────────────────────────
        if self.path == '/chat/kimi':
            messages = body.get('messages', [])
            max_tok = body.get('max_tokens', 16000)
            temperature = body.get('temperature', 1.0)
            if not KIMI_KEY:
                self._send_json(500, {'error': 'KIMI_API_KEY 未配置'})
                return
            payload = {
                'model': 'kimi-k2.7-code',
                'messages': messages,
                'max_tokens': max_tok,
                'temperature': temperature,
            }
            req = urllib.request.Request(
                'https://api.moonshot.cn/v1/chat/completions',
                data=json.dumps(payload).encode(),
                headers={'Authorization': f'Bearer {KIMI_KEY}', 'Content-Type': 'application/json'}
            )
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    r = json.loads(resp.read())
                if 'error' in r:
                    self._send_json(500, {'error': r['error']})
                else:
                    self._send_json(200, {
                        'content': r['choices'][0]['message']['content'],
                        'usage': r.get('usage', {}),
                    })
            except Exception as e:
                self._send_json(500, {'error': str(e)})

        # ─── Vision ─────────────────────────────────────────
        elif self.path == '/vision/step':
            img = body.get('image', '')
            prompt = body.get('prompt', '描述这张图片')
            max_tok = body.get('max_tokens', 400)
            result = step_vision(img, prompt, max_tok)
            code = 200 if 'content' in result else 500
            self._send_json(code, result)

        elif self.path == '/vision/kimi':
            img = body.get('image', '')
            prompt = body.get('prompt', '描述这张图片')
            max_tok = body.get('max_tokens', 2000)
            result = kimi_vision(img, prompt, max_tok)
            code = 200 if 'content' in result else 500
            self._send_json(code, result)

        # ─── Image Generate ─────────────────────────────────
        elif self.path == '/image/generate':
            prompt = body.get('prompt', '')
            size = body.get('size', '1024x1024')
            steps = body.get('steps', 8)
            payload = {
                'model': 'step-image-edit-2',
                'prompt': prompt,
                'n': 1,
                'size': size,
                'response_format': 'b64_json',
                'steps': steps,
            }
            req = urllib.request.Request(
                'https://api.stepfun.com/v1/images/generations',
                data=json.dumps(payload).encode(),
                headers={'Authorization': f'Bearer {STEP_KEY}', 'Content-Type': 'application/json'}
            )
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    r = json.loads(resp.read())
                if 'error' in r:
                    self._send_json(500, {'error': r['error']})
                else:
                    img_b64 = r['data'][0]['b64_json']
                    self._send_json(200, {'image': img_b64, 'status': 'ok'})
            except Exception as e:
                self._send_json(500, {'error': str(e)})

        # ─── Image Edit ─────────────────────────────────────
        elif self.path == '/image/edit':
            img = body.get('image', '')
            prompt = body.get('prompt', '')
            seed = body.get('seed', None)
            output = body.get('output', '/tmp/step-edit-gateway.png')
            result = step_edit(img, prompt, seed=seed)
            if 'images' in result and result['images'] > 0:
                result['output'] = output
                result['status'] = 'ok'
            self._send_json(200 if 'status' in result else 500, result)

        # ─── Batch Analyze (识图 → 分析 → 优化建议) ─────────
        elif self.path == '/workflow/analyze-and-fix':
            images = body.get('images', [])  # list of image paths
            results = []
            for img_path in images:
                # Step 1: 分析
                full_path = os.path.expanduser(img_path)
                analysis = step_vision(full_path, "List 3 UI issues in this screenshot, short answer, Chinese.", 250)
                analysis_text = analysis.get('content', '(empty)')

                # Step 2: 根据分析生成修复 prompt
                fix_prompt = f"Fix these UI issues while keeping the layout: {analysis_text[:200]}"

                results.append({
                    'image': img_path,
                    'analysis': analysis_text,
                    'fix_prompt': fix_prompt,
                })

            self._send_json(200, {'results': results})

        else:
            self._send_json(404, {'error': f'unknown endpoint: {self.path}'})

# ─── Main ──────────────────────────────────────────────────
def main():
    server = HTTPServer(('127.0.0.1', PORT), GatewayHandler)
    print(f"🌐 多模态网关: http://127.0.0.1:{PORT}")
    print(f"   /health              — 健康检查")
    print(f"   /chat/kimi           — Kimi 纯文本聊天")
    print(f"   /vision/step         — Step 识图")
    print(f"   /vision/kimi         — Kimi 识图")
    print(f"   /image/generate      — 文生图")
    print(f"   /image/edit          — 图像编辑")
    print(f"   /workflow/analyze-and-fix — 批量分析")
    print()

    def shutdown(sig, frame):
        print("\n👋 网关关闭")
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        shutdown(None, None)

if __name__ == '__main__':
    main()
