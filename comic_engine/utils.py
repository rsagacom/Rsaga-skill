"""通用工具函数"""
import base64
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
    """加载分镜脚本（Python 模块形式）"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("storyboard", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, "PANELS", [])
