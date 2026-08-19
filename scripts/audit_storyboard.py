#!/usr/bin/env python3
"""分镜提示词审核 — 生图前用大模型逐格审 prompt

用法:
    python3 scripts/audit_storyboard.py projects/bridge-god/ch03/storyboard_ch03.py
    python3 scripts/audit_storyboard.py <storyboard> --provider kimi       # 官方 moonshot K2.7(走 gateway)
    python3 scripts/audit_storyboard.py <storyboard> --provider ds          # DeepSeek 官方 API(直连, OpenAI 兼容)
    python3 scripts/audit_storyboard.py <storyboard> --limit 3              # 先试 N 格

provider 说明:
    kimi  — 走本地 gateway /chat/kimi (model kimi-k2.7-code, key 来自 gateway 启动环境)
    ds    — 直连 DeepSeek 官方 API (默认 deepseek-v4-flash-260425, key 读 DS_API_KEY 环境变量或 --ds-key)
"""
import argparse, json, sys, time, urllib.request, importlib.util, os
from pathlib import Path

GATEWAY = "http://127.0.0.1:11939"
DS_BASE = "https://api.deepseek.com"
GPT_GATEWAY = os.environ.get("GPT_GATEWAY_URL", "").rstrip("/")
GPT_KEY = os.environ.get("GPT_GATEWAY_API_KEY", "")

# ─── 审核维度 ──────────────────────────────────────────────
AUDIT_SYSTEM = """你是漫画分镜审核专家。我给你画格的生图 prompt 和审核类型，审核后输出 JSON。

⚠️ 核心要求：修正后的 suggestion 必须用**中文**写 prompt。Step 模型对中文理解精准，
中文 prompt 信息密度高、画面感强。不要用英文。

注意: 我提供的 Q 定义里如果含 "thin black-frame glasses"，这是已知错误，正确应为
"细金属框眼镜"(thin wire-rim)，你需要在 issues 里标出。

输出一个 JSON 对象(不是数组，因为每次只审1格)，字段：
- "pid": 画格ID
- "verdict": "通过" | "需修改" | "严重问题"
- "issues": [问题列表，每条≤25字]
- "suggestion": 修正后的完整中文 prompt（若verdict=通过则留空字符串）

中文 prompt 规范：
- 全程中文描述画面，信息密度高，含：镜头角度/光影/构图/角色动作/氛围
- 用 {Q} 代替主角完整描述，用 {S} 代替画风后缀
- 控制在 120 字以内(不含{Q}{S})，给 {Q} 展开留字节空间
- {Q} 和 {S} 作为占位符直接写，例: "{Q}低头坐在便利店窗边，冷光斜照侧脸，眼神空洞，{S}"

审核维度：
1. 【单调】构图雷同(特写/远景/抽象重复) → 建议换镜头角度/光影
2. 【抽象难画】太虚的抽象描述(如"扩张的黑暗""时间线") → 改具体可视画面
3. 【逻辑矛盾】童年场景用成人、超自然有五官、尸体在动、主角被写成病人
4. 【角色一致性】眼镜应为细金属框、服装/发型漂移
5. 【中英混杂】原 prompt 混入英文/中文短语 → 统一中文
6. 【图内文字污染】不要要求生图模型生成可读文字、帖子、报告正文、门牌、标题或 SFX；这些文字应交给 BUBBLE_CONFIG / 排版层
7. 【真人照片感】不得把 realistic/photograph/photo-realistic/live-action/写实风格 当正向画风词；画风必须保留东亚黑白漫画、2D comic、NOT photorealistic/NOT realistic face
8. 【剪影角色】房东/租客/模糊反派/无名人物应为剪影、背影或虚影；如果出现笑容、嘴、鼻、牙、完整脸，需要改为无五官约束
9. 【否定画风约束】prompt(含 {S} 展开)必须含 NOT photo / NOT photorealistic / NOT realistic face 之一(或中文"非照片/非写实")；缺失时标为需修改，提示补回否定约束避免真人照片脸
10. 【文字载体】prompt 涉及论坛、报告、手机屏幕、报纸、海报、标题、SFX 时，必须写"无可读文字/no readable text/文字交给排版"；要求生图模型画可读文字、帖子正文、报告正文、标题字样的，标为严重问题

只看 prompt 文字，不生图。直接输出 { 开头的 JSON 对象。"""

# ─── 加载 storyboard ────────────────────────────────────────
def load_storyboard(path):
    spec = importlib.util.spec_from_file_location("sb", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    q = getattr(mod, "Q", "")
    s = getattr(mod, "S", "")
    panels = []
    for p in getattr(mod, "PANELS", []):
        pid, prompt = p[0], p[1]
        atype = p[2] if len(p) > 2 else "scene"
        # 还原 {Q}/{S} 占位给审核看(也看实际值)
        if q and "{Q}" in prompt:
            prompt = prompt.replace("{Q}", q)
        if s and "{S}" in prompt:
            prompt = prompt.replace("{S}", s)
        panels.append((pid, prompt, atype))
    return panels, q, s

# ─── 调 gateway (官方 Kimi) ─────────────────────────────────
def kimi_chat(messages, max_tokens=4000):
    payload = json.dumps({
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }).encode()
    req = urllib.request.Request(
        f"{GATEWAY}/chat/kimi",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read())

# ─── 调 DeepSeek 官方 API (OpenAI 兼容协议) ────────────
DS_CHAT = "https://api.deepseek.com/v1/chat/completions"

def ds_chat(messages, max_tokens=None, model="deepseek-v4-flash-260425", api_key=None):
    """DeepSeek 官方 API，OpenAI 兼容协议。

    messages 格式同 OpenAI(role/content)。
    max_tokens: DS V4 Flash 不需要 thinking 配额，4096 足够。
    """
    if not api_key:
        raise RuntimeError("DS key 未提供: 设 DS_API_KEY 环境变量或 --ds-key")

    if max_tokens is None:
        max_tokens = 4096

    payload = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "messages": messages,
    }).encode()
    req = urllib.request.Request(
        DS_CHAT,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read())

# ─── 调本地 GPT 网关 ───────────────────────────────────
def gpt_chat(messages, max_tokens=4096, model="gpt-5-3"):
    if not GPT_GATEWAY or not GPT_KEY:
        raise RuntimeError("GPT provider 未配置: 请设置 GPT_GATEWAY_URL 和 GPT_GATEWAY_API_KEY")
    payload = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "messages": messages,
    }).encode()
    req = urllib.request.Request(
        f"{GPT_GATEWAY}/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {GPT_KEY}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read())

# ─── main ────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="分镜提示词审核")
    ap.add_argument("storyboard", help="storyboard_chXX.py 路径")
    ap.add_argument("--provider", "-p", default="gpt",
                    choices=["kimi", "ds", "gpt"], help="kimi=官方moonshot(走gateway) / ds=DeepSeek官方API / gpt=本地GPT网关")
    ap.add_argument("--ds-model", default="deepseek-v4-flash-260425",
                    help="DeepSeek model ID (默认 deepseek-v4-flash-260425)")
    ap.add_argument("--gpt-model", default="gpt-5-3",
                    help="GPT model ID (默认 gpt-5-3)")
    ap.add_argument("--ds-key", default=None, help="DS key(默认读 DS_API_KEY 环境变量)")
    ap.add_argument("--limit", "-n", type=int, default=0, help="只审前 N 格(0=全部)")
    ap.add_argument("--batch", "-b", type=int, default=1, help="每批发送格数(K2.7-code 思考重,建议1)")
    args = ap.parse_args()

    panels, q, s = load_storyboard(args.storyboard)
    print(f"📖 加载 {len(panels)} 格  Q={q[:40]}...  S={s[:40]}...")
    model_info = f" model={args.gpt_model}" if args.provider=="gpt" else (f" model={args.ds_model}" if args.provider=="ds" else "")
    print(f"   provider={args.provider}{model_info}")
    if args.limit:
        panels = panels[:args.limit]
        print(f"   (限前 {args.limit} 格)")

    ds_key = args.ds_key or os.environ.get("DS_API_KEY", "")

    def call_llm(messages):
        """统一调用入口，返回 dict(含 content/error)"""
        if args.provider == "kimi":
            return kimi_chat(messages)
        elif args.provider == "gpt":
            r = gpt_chat(messages, model=args.gpt_model)
            if "error" in r:
                return r
            content = r["choices"][0]["message"]["content"]
            return {"content": content,
                    "usage": r.get("usage", {}),
                    "truncated": r.get("stop_reason") == "max_tokens"}
        else:
            r = ds_chat(messages, model=args.ds_model, api_key=ds_key)
            if "error" in r:
                return r
            content = r["choices"][0]["message"]["content"]
            return {"content": content,
                    "usage": r.get("usage", {}),
                    "truncated": r.get("stop_reason") == "max_tokens"}

    all_results = []
    total_batches = (len(panels) + args.batch - 1) // args.batch

    for bi in range(total_batches):
        chunk = panels[bi*args.batch:(bi+1)*args.batch]
        # 构造 user 消息：列出这批 prompt
        lines = [f"Q 定义: {q}", f"S 定义: {s}", "", "待审核画格:"]
        for pid, prompt, atype in chunk:
            lines.append(f"[{pid}] 类型={atype}")
            lines.append(f"prompt: {prompt}")
            lines.append("")
        user_msg = "\n".join(lines)

        print(f"\n🔍 审核批次 {bi+1}/{total_batches} ({len(chunk)} 格)...")
        try:
            resp = call_llm([
                {"role": "system", "content": AUDIT_SYSTEM},
                {"role": "user", "content": user_msg},
            ])
            if "error" in resp:
                print(f"  ❌ 错误: {resp['error']}")
                errstr = str(resp['error'])
                # 额度/限流/401 时直接停
                if any(k in errstr for k in ["余额", "quota", "insufficient", "401",
                                              "429", "Too Many", "limit"]):
                    print("  ⛔ 限流或无额度，终止。可换 --provider ds 重试。")
                    break
                time.sleep(5)
                continue
            content = resp.get("content", "")
            print(f"  (tokens: {resp.get('usage', {})})")
        except Exception as e:
            print(f"  ❌ 异常: {e}")
            break

        # 解析 JSON
        try:
            # 容错：去掉可能的 markdown 包裹
            clean = content.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            results = json.loads(clean)
            if isinstance(results, dict):
                results = [results]
            all_results.extend(results)
            for r in results:
                v = r.get("verdict", "?")
                icon = {"通过": "✅", "需修改": "⚠️", "严重问题": "❌"}.get(v, "?")
                print(f"  {icon} {r.get('pid','?')}: {v}")
                for iss in r.get("issues", [])[:2]:
                    print(f"       → {iss}")
        except json.JSONDecodeError:
            # 尝试从内容里提取第一个 {...} 或 [...]
            import re as _re
            m = _re.search(r'[\[{][\s\S]*[}\]]', content)
            if m:
                try:
                    results = json.loads(m.group(0))
                    if isinstance(results, dict):
                        results = [results]
                    all_results.extend(results)
                    for r in results:
                        v = r.get("verdict", "?")
                        icon = {"通过": "✅", "需修改": "⚠️", "严重问题": "❌"}.get(v, "?")
                        print(f"  {icon} {r.get('pid','?')}: {v}")
                        for iss in r.get("issues", [])[:2]:
                            print(f"       → {iss}")
                    continue
                except json.JSONDecodeError:
                    pass
            print(f"  ⚠️  JSON 解析失败，原文:")
            print("  " + content[:500].replace("\n", "\n  "))
        time.sleep(2)

    # 汇总
    print(f"\n{'='*50}")
    print(f"审核完成: {len(all_results)}/{len(panels)} 格")
    by_verdict = {}
    for r in all_results:
        v = r.get("verdict", "?")
        by_verdict[v] = by_verdict.get(v, 0) + 1
    for v, n in by_verdict.items():
        print(f"  {v}: {n}")

    # 保存报告
    out = Path(args.storyboard).with_name(
        Path(args.storyboard).stem + "_audit.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n报告: {out}")

    # 保存可用的修正 prompt(供生成新 storyboard)
    suggestions = {r["pid"]: r["suggestion"] for r in all_results
                   if r.get("suggestion")}
    if suggestions:
        sug_path = Path(args.storyboard).with_name(
            Path(args.storyboard).stem + "_fixes.json")
        with open(sug_path, "w", encoding="utf-8") as f:
            json.dump(suggestions, f, ensure_ascii=False, indent=2)
        print(f"修正建议: {sug_path} ({len(suggestions)} 格)")

if __name__ == "__main__":
    main()
