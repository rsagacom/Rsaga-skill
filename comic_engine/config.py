"""配置加载模块"""
import os
from pathlib import Path

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def load_config(config_path=None):
    """加载配置文件"""
    if config_path is None:
        # 先查找项目根目录下的 config.yaml
        candidates = [
            Path.cwd() / "config.yaml",
            Path(__file__).parent.parent / "config.yaml",
        ]
        for c in candidates:
            if c.exists():
                config_path = c
                break

    if config_path is None or not Path(config_path).exists():
        raise FileNotFoundError(
            "未找到 config.yaml，请复制 config.yaml.example 为 config.yaml 并填入 API key"
        )

    config_path = Path(config_path)

    if HAS_YAML:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    else:
        # 如果没有 PyYAML，使用简单解析
        raw = _parse_simple_yaml(config_path)

    # 展开环境变量
    config = _expand_env(raw)
    config["_config_path"] = str(config_path)
    return config


def _expand_env(obj):
    """递归展开字符串中的 ${ENV} 或 $ENV"""
    if isinstance(obj, dict):
        return {k: _expand_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env(i) for i in obj]
    if isinstance(obj, str):
        return os.path.expandvars(obj)
    return obj


def _parse_simple_yaml(path):
    """极简 YAML 解析（仅支持 key: value 两层结构）"""
    result = {}
    current_section = None
    current_dict = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip()
            if not line or line.strip().startswith("#"):
                continue
            stripped = line.strip()
            if stripped.endswith(":") and not line.startswith(" "):
                current_section = stripped[:-1]
                result[current_section] = {}
                current_dict = result[current_section]
            elif line.startswith("  ") and stripped.endswith(":"):
                current_key = stripped[:-1]
                current_dict[current_key] = {}
            elif line.startswith("    ") and ":" in stripped:
                k, v = stripped.split(":", 1)
                current_dict[k.strip()] = v.strip().strip('"').strip("'")
            elif ":" in stripped and current_dict is not None:
                k, v = stripped.split(":", 1)
                current_dict[k.strip()] = v.strip().strip('"').strip("'")
    return result


def get_api_key(config, provider_type, provider_name):
    """获取 API key"""
    key = (
        config.get("providers", {})
        .get(provider_type, {})
        .get(provider_name, {})
        .get("api_key", "")
    )
    if not key:
        env_var = f"{provider_name.upper()}_API_KEY"
        key = os.environ.get(env_var, "")
    return key
