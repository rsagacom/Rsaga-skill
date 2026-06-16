#!/usr/bin/env python3
"""
批量生图 — 基于 storyboard_chXX.py 生成画格
模型：Step Image Edit 2（通过本地 mm-gateway 调用）
"""
import os, json, time, sys, pathlib, importlib.util, base64, argparse, urllib.request

GATEWAY = "http://127.0.0.1:11939/image/generate"
SIZE = "1024x1024"
RETRY_DELAY = 10
MAX_RETRIES = 5


def load_storyboard(path):
    """动态导入 storyboard 文件，获取 PANELS 列表，替换 {Q} 和 {S}"""
    spec = importlib.util.spec_from_file_location("storyboard", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    panels = []
    for p in mod.PANELS:
        pid, prompt = p[0], p[1]
        atype = p[2] if len(p) > 2 else "unknown"
        # 替换 {Q} 和 {S} 为实际值
        if hasattr(mod, 'Q') and '{Q}' in prompt:
            prompt = prompt.replace('{Q}', mod.Q)
        if hasattr(mod, 'S') and '{S}' in prompt:
            prompt = prompt.replace('{S}', mod.S)
        panels.append((pid, prompt, atype))
    return panels


def generate_panel(panel_id, prompt, out_dir, force=False):
    """通过 gateway 生成单个画格"""
    safe_name = panel_id.replace("/", "_").replace(" ", "_")
    out_path = out_dir / f"{safe_name}.png"

    if out_path.exists() and not force:
        return "skip"

    print(f"  🎨 {safe_name}", flush=True)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            payload = json.dumps({"prompt": prompt, "size": SIZE, "steps": 8}).encode()
            req = urllib.request.Request(
                GATEWAY,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                r = json.loads(resp.read())

            if "error" in r:
                print(f"    ⚠️  API error: {r['error']}", flush=True)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
                    continue
                return "error"

            img_b64 = r.get("image", "")
            if not img_b64:
                return "error"

            img_data = base64.b64decode(img_b64)
            with open(out_path, "wb") as f:
                f.write(img_data)
            return "ok"

        except Exception as e:
            print(f"    ⚠️  {attempt}/{MAX_RETRIES}: {e}", flush=True)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
            else:
                return "error"


def main():
    parser = argparse.ArgumentParser(description="批量生图（通过本地 gateway）")
    parser.add_argument("storyboard", help="storyboard_chXX.py 路径")
    parser.add_argument("--out", "-o", help="输出目录")
    parser.add_argument("--force", action="store_true", help="强制覆盖已有画格")
    parser.add_argument("--dry-run", action="store_true", help="只预览不生成")
    parser.add_argument("--concurrent", "-c", type=int, default=1, help="并发数（默认1）")
    args = parser.parse_args()

    print(f"📖 加载分镜脚本: {args.storyboard}")
    panels = load_storyboard(args.storyboard)

    if args.out:
        out_dir = pathlib.Path(args.out)
    else:
        parts = pathlib.Path(args.storyboard).parts
        if "projects" in parts:
            idx = parts.index("projects")
            project_name = parts[idx + 1]
            chapter = parts[idx + 2] if len(parts) > idx + 2 else "chapter"
            out_dir = pathlib.Path.home() / "Desktop" / f"{project_name}-{chapter}" / "panels"
        else:
            out_dir = pathlib.Path.home() / "Desktop" / "comic-output" / "panels"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"📁 输出: {out_dir}")

    review_types = {}
    for p in panels:
        rt = p[2] if len(p) > 2 else "unknown"
        review_types[rt] = review_types.get(rt, 0) + 1
    print(f"📊 共 {len(panels)} 个画格: {review_types}")

    if args.dry_run:
        for i, p in enumerate(panels):
            print(f"  [{i+1}/{len(panels)}] {p[0]}")
        return

    import concurrent.futures
    results = {"ok": 0, "skip": 0, "error": 0}

    if args.concurrent > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrent) as pool:
            futures = {}
            for i, panel in enumerate(panels):
                panel_id, prompt = panel[0], panel[1]
                fut = pool.submit(generate_panel, panel_id, prompt, out_dir, args.force)
                futures[fut] = (i + 1, panel_id)

            for fut in concurrent.futures.as_completed(futures):
                idx, pid = futures[fut]
                result = fut.result()
                results[result] = results.get(result, 0) + 1
                icon = {"ok": "✅", "skip": "⏭️", "error": "❌"}.get(result, "?")
                print(f"[{idx}/{len(panels)}] {pid} {icon}", flush=True)
    else:
        for i, panel in enumerate(panels):
            panel_id, prompt = panel[0], panel[1]
            print(f"\n[{i+1}/{len(panels)}] ", end="", flush=True)
            result = generate_panel(panel_id, prompt, out_dir, args.force)
            results[result] = results.get(result, 0) + 1
            icon = {"ok": "✅", "skip": "⏭️", "error": "❌"}.get(result, "?")
            print(f"  {icon}", flush=True)

    print(f"\n{'='*40}")
    print(f"✅ 成功: {results.get('ok', 0)}")
    print(f"⏭️  跳过: {results.get('skip', 0)}")
    print(f"❌ 失败: {results.get('error', 0)}")
    print(f"📁 {out_dir}")


if __name__ == "__main__":
    main()
