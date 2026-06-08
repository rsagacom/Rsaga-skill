#!/usr/bin/env python3
"""修复单张问题画格

用法:
    python3 scripts/fix_panel.py projects/example/panels/P10_画格1_法医检查.png "修正后的 prompt"
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from comic_engine.config import load_config
from comic_engine.generator import generate_panel


def main():
    parser = argparse.ArgumentParser(description="修复单张画格")
    parser.add_argument("panel_path", help="画格文件路径")
    parser.add_argument("prompt", help="修正后的英文 prompt")
    parser.add_argument("--config", "-c", default=None, help="配置文件路径")
    args = parser.parse_args()

    config = load_config(args.config)
    providers = config.get("providers", {})
    image_cfg = providers.get("image", {})
    provider_name = image_cfg.get("default", "step")
    provider = image_cfg.get(provider_name, {})

    api_key = provider.get("api_key", "")
    api_url = provider.get("api_url", "https://api.stepfun.com/v1")
    model = provider.get("model", "step-image-edit-2")
    size = provider.get("size", "1024x1024")

    if not api_key:
        print("错误：未配置 API key")
        sys.exit(1)

    panel_path = Path(args.panel_path)
    print(f"修复: {panel_path.name}")
    print(f"删除旧图...")
    panel_path.unlink(missing_ok=True)

    print(f"重新生成...")
    img = generate_panel(args.prompt, api_key, api_url, model, size)
    with open(panel_path, "wb") as f:
        f.write(img)
    print(f"✓ {panel_path.name} 已修复 ({len(img) // 1024}KB)")


if __name__ == "__main__":
    main()
