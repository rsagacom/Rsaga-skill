"""画格生成器"""
import json
import time
import urllib.request
from pathlib import Path

from .config import get_api_key


def generate_panel(prompt, api_key, api_url, model, size="1024x1024", timeout=180):
    """生成单张画格，返回图片二进制数据"""
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


def generate_panels(panels, config, output_dir, sleep_seconds=6, max_retries=3):
    """批量生成画格

    Args:
        panels: list of (name, prompt) tuples
        config: loaded config dict
        output_dir: Path to output directory
        sleep_seconds: seconds to sleep between requests
        max_retries: max retry attempts per panel

    Returns:
        list of dicts with name, path, size, status
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

    results = []
    for i, (name, prompt) in enumerate(panels, 1):
        print(f"  [{i:2d}/{len(panels)}] {name} ...", end=" ", flush=True)
        path = output_dir / f"{name}.png"
        status = "ok"
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
                if attempt == max_retries - 1:
                    print(f"✗ {e}")
                    status = f"error: {e}"
                else:
                    time.sleep(10)

        results.append({
            "name": name,
            "path": str(path),
            "size_kb": size_kb,
            "status": status,
        })
        time.sleep(sleep_seconds)

    return results
