"""画格生成器"""
import json
import time
import urllib.request
from pathlib import Path

from .config import get_api_key


def generate_panel(prompt, api_key, api_url, model, size="1024x1024", timeout=180):
    """生成单张画格，返回 (图片二进制数据, None) 或异常时 raise"""
    payload = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
    }
    req = urllib.request.Request(
        f"{api_url}/images/generations",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    image_url = data["data"][0]["url"]
    with urllib.request.urlopen(image_url, timeout=120) as r:
        return r.read()


def classify_error(e):
    """分类错误类型。

    Returns:
        str: "rate_limited" | "bad_prompt" | "network_error" | "unknown"
    """
    msg = str(e).lower()
    if "429" in msg or "rate limit" in msg or "too many requests" in msg:
        return "rate_limited"
    if "400" in msg or "bad request" in msg or "invalid" in msg:
        return "bad_prompt"
    if isinstance(e, (urllib.error.URLError, ConnectionError, TimeoutError, OSError)):
        return "network_error"
    return "unknown"


def generate_panels(panels, config, output_dir, sleep_seconds=6, max_retries=3,
                     resume=True, force=False):
    """批量生成画格

    Args:
        panels: list of (name, prompt) 或 (name, prompt, audit_type) tuples
        config: loaded config dict
        output_dir: Path to output directory
        sleep_seconds: seconds to sleep between requests
        max_retries: max retry attempts per panel
        resume: 若为 True，跳过已存在的画格（断点续传）
        force: 若为 True，即使 resume=True 也强制重新生成

    Returns:
        list of dicts with name, path, size, status, error_type
    """
    providers = config.get("providers", {})
    image_cfg = providers.get("image", {})
    provider_name = image_cfg.get("default", "step")
    provider = image_cfg.get(provider_name, {})

    api_key = get_api_key(config, "image", provider_name)
    api_url = provider.get("api_url", "https://api.stepfun.com/v1")
    model = provider.get("model", "step-image-edit-2")
    size = provider.get("size", "1024x1024")

    if not api_key:
        raise ValueError(f"未配置 {provider_name} 的 API key")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 断点续传：统计已存在的画格
    skipped = 0
    pending = []
    for item in panels:
        if len(item) == 3:
            name, prompt, audit_type = item
        else:
            name, prompt = item
        path = output_dir / f"{name}.png"
        if resume and not force and path.exists():
            skipped += 1
        else:
            pending.append((name, prompt, path))

    total = len(panels)
    if skipped > 0:
        print(f"  ↳ 断点续传：跳过 {skipped} 个已有画格，待生成 {len(pending)} 个")

    results = []
    # 为跳过的画格生成结果记录
    if resume and not force:
        for item in panels:
            if len(item) == 3:
                name, prompt, audit_type = item
            else:
                name, prompt = item
            path = output_dir / f"{name}.png"
            if path.exists():
                size_kb = path.stat().st_size // 1024
                results.append({
                    "name": name,
                    "path": str(path),
                    "size_kb": size_kb,
                    "status": "skipped",
                    "error_type": None,
                })

    for i, (name, prompt, path) in enumerate(pending, 1):
        progress = f"[{i:2d}/{len(pending)}]"
        print(f"  {progress} {name} ... ", end="", flush=True)
        status = "ok"
        error_type = None
        size_kb = 0

        for attempt in range(max_retries):
            try:
                img = generate_panel(prompt, api_key, api_url, model, size)
                with open(path, "wb") as f:
                    f.write(img)
                size_kb = len(img) // 1024
                print(f"✓ {size_kb}KB")
                break
            except Exception as e:
                error_type = classify_error(e)
                if error_type == "bad_prompt":
                    # 400 错误：不重试，prompt 本身有问题
                    print(f"✗ BAD PROMPT: {str(e)[:80]}")
                    status = f"bad_prompt: {e}"
                    break
                elif attempt == max_retries - 1:
                    print(f"✗ {error_type}: {str(e)[:60]}")
                    status = f"error: {e}"
                else:
                    wait = 10 if error_type == "network_error" else 15
                    time.sleep(wait)

        results.append({
            "name": name,
            "path": str(path),
            "size_kb": size_kb,
            "status": status,
            "error_type": error_type,
        })
        time.sleep(sleep_seconds)

    return results


def estimate_resources(panels, sleep_seconds=6):
    """估算生成资源（dry-run）。

    Returns:
        dict with panel_count, estimated_minutes, estimated_cost_note
    """
    count = len(panels)
    # 每格 ~7 秒（含 API 调用 1 秒 + sleep 6 秒）
    est_seconds = count * (sleep_seconds + 1)
    est_minutes = est_seconds / 60
    return {
        "panel_count": count,
        "sleep_seconds": sleep_seconds,
        "estimated_seconds": est_seconds,
        "estimated_minutes": round(est_minutes, 1),
        "cost_note": "费用取决于 API provider 定价，请参考 StepFun 官方价目表",
    }
