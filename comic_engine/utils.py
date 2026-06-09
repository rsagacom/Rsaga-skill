"""通用工具函数"""
import base64
import sys
import urllib.request
import json
from pathlib import Path


def encode_image_to_base64(image_path):
    """将图片编码为 base64"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def api_post(url, payload, api_key, timeout=120):
    """通用 API POST 请求"""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def download_image(url, timeout=120):
    """从 URL 下载图片"""
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def ensure_dir(path):
    """确保目录存在"""
    Path(path).mkdir(parents=True, exist_ok=True)


def load_storyboard(path):
    """加载分镜脚本（Python 模块形式）。

    支持 PANELS 为以下格式:
        [(name, prompt), ...]          — 2 元素
        [(name, prompt, audit_type), ...]  — 3 元素（推荐）

    Returns:
        list of tuples
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("storyboard", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    panels = getattr(mod, "PANELS", [])
    # 向后兼容：2 元素格式填充为 3 元素
    normalized = []
    for item in panels:
        if len(item) == 2:
            normalized.append((item[0], item[1], None))
        elif len(item) >= 3:
            normalized.append((item[0], item[1], item[2]))
        else:
            raise ValueError(f"PANELS 格式错误: {item}，需要至少 (name, prompt)")
    return normalized


def detect_platform_fonts():
    """检测当前平台的可用中文字体路径。

    Returns:
        dict with "title", "body", "light" keys
    """
    if sys.platform == "darwin":
        return {
            "title": "/System/Library/Fonts/STHeiti Medium.ttc",
            "body": "/System/Library/Fonts/STHeiti Medium.ttc",
            "light": "/System/Library/Fonts/STHeiti Light.ttc",
        }
    elif sys.platform == "win32":
        import os
        windir = os.environ.get("WINDIR", "C:\\Windows")
        return {
            "title": f"{windir}\\Fonts\\simhei.ttf",
            "body": f"{windir}\\Fonts\\simsun.ttc",
            "light": f"{windir}\\Fonts\\simsun.ttc",
        }
    else:
        # Linux / other
        candidates = [
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        ]
        for c in candidates:
            if Path(c).exists():
                return {"title": c, "body": c, "light": c}
        # 最后的 fallback
        return {
            "title": "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "body": "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "light": "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        }
