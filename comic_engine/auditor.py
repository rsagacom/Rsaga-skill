"""视觉审核模块"""
import json
import time
import urllib.request
from pathlib import Path

from .config import get_api_key
from .utils import encode_image_to_base64


# 默认审核规则库（通用 fallback，不含项目特定关键词）
DEFAULT_AUDIT_RULES = {
    "character": {
        "description": "角色外貌一致性",
        "prompt": """审核要求：检查图中角色是否符合描述。
请逐一检查：
1. 发型、发色是否与描述一致？
2. 是否佩戴指定眼镜？
3. 服装是否与描述一致？
4. 画面是否出现重影/叠加/双重人像？

只输出发现的问题，每条一行，格式：「问题类型：具体描述」。如无问题，输出「通过」。"""
    },
    "supernatural": {
        "description": "超自然实体",
        "prompt": """审核要求：检查超自然实体。

1. 是否只有发光眼窝，无完整人脸？
2. 是否有嘴巴/鼻子等面部细节？
3. 身形是否半透明/烟雾质感？
4. 是否出现重影/叠加？

只输出发现的问题。如无问题，输出「通过」。"""
    },
    "hand": {
        "description": "手部绘制质量",
        "prompt": """审核要求：检查手部绘制质量。

1. 手指数量是否为5根？
2. 手掌轮廓是否正常？
3. 是否过度写实到不自然？

只输出发现的问题。如无问题，输出「通过」。"""
    },
    "scene": {
        "description": "场景合规",
        "prompt": """审核要求：检查场景合规。

1. 是否有不应出现的儿童？
2. 空旷场景是否有无关人群？
3. 是否出现与剧情时代不符的元素？

只输出发现的问题。如无问题，输出「通过」。"""
    },
    "abstract": {
        "description": "抽象/隐喻画面",
        "prompt": """审核要求：检查抽象/隐喻画面。

1. 是否出现人物重影/叠加？
2. 画面是否有撕裂/扭曲？
3. 超自然实体是否出现完整人脸？

只输出发现的问题。如无问题，输出「通过」。"""
    }
}


def load_audit_rules(config=None, project_dir=None):
    """加载审核规则（按优先级合并）。

    优先级: 项目 audit_rules.yaml > config.yaml audit.rules > DEFAULT_AUDIT_RULES

    Args:
        config: loaded config dict (optional)
        project_dir: project directory Path (optional), checks audit_rules.yaml

    Returns:
        dict of audit rules
    """
    import os

    rules = dict(DEFAULT_AUDIT_RULES)  # shallow copy

    # 从 config.yaml 加载
    if config:
        custom = config.get("audit", {}).get("rules", {})
        if isinstance(custom, dict):
            rules.update(custom)

    # 从项目级 audit_rules.yaml 加载（最高优先级）
    if project_dir:
        rules_path = Path(project_dir) / "audit_rules.yaml"
        if rules_path.exists():
            try:
                import yaml
                with open(rules_path, "r", encoding="utf-8") as f:
                    project_rules = yaml.safe_load(f)
                if isinstance(project_rules, dict):
                    rules.update(project_rules)
            except ImportError:
                # 无 PyYAML 时用简单解析
                project_rules = _parse_simple_audit_yaml(rules_path)
                if project_rules:
                    rules.update(project_rules)

    return rules


def _parse_simple_audit_yaml(path):
    """极简 audit_rules.yaml 解析（无 PyYAML fallback）"""
    result = {}
    current_type = None
    current_rule = {}
    in_prompt = False
    prompt_lines = []

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            raw = line.rstrip()
            # 跳过空行和注释
            if not raw or raw.strip().startswith("#"):
                continue

            # 检测是否在 prompt 多行字符串内
            if in_prompt:
                if raw.startswith("    "):
                    prompt_lines.append(raw.strip())
                    continue
                else:
                    current_rule["prompt"] = "\n".join(prompt_lines)
                    prompt_lines = []
                    in_prompt = False
                    # 保存规则
                    if current_type and current_rule:
                        result[current_type] = current_rule
                        current_rule = {}

            # 顶级 key（审核类型）
            if raw.endswith(":") and not raw.startswith(" "):
                if current_type and current_rule:
                    result[current_type] = current_rule
                current_type = raw[:-1]
                current_rule = {}
                continue

            # 子字段
            if ":" in raw and raw.startswith("  ") and not raw.startswith("    "):
                k, v = raw.split(":", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k == "prompt" and (v == "|" or v == ">"):
                    in_prompt = True
                    prompt_lines = []
                else:
                    current_rule[k] = v

    # 处理最后一个
    if in_prompt and prompt_lines:
        current_rule["prompt"] = "\n".join(prompt_lines)
    if current_type and current_rule:
        result[current_type] = current_rule

    return result


def classify_panel(name, audit_type=None, rules=None):
    """分类画格审核类型。

    优先级:
    1. 显式 audit_type 参数（来自 PANELS 元组的第 3 元素）
    2. 关键词匹配（向后兼容旧格式）
    3. 默认 "scene"

    Args:
        name: 画格名称
        audit_type: 显式审核类型（可选）
        rules: 审核规则 dict，用于关键词匹配

    Returns:
        str: 审核类型
    """
    if audit_type:
        return audit_type

    if rules is None:
        rules = DEFAULT_AUDIT_RULES

    # 向后兼容：关键词匹配
    for rule_type, rule in rules.items():
        keywords = rule.get("keywords", [])
        for kw in keywords:
            if kw in name:
                return rule_type

    return "scene"


def audit_panel(image_path, prompt, api_key, api_url, model, max_tokens=400, timeout=60):
    """对单张画格进行视觉审核"""
    img_b64 = encode_image_to_base64(image_path)
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                {"type": "text", "text": prompt}
            ]
        }],
        "max_tokens": max_tokens
    }
    req = urllib.request.Request(
        f"{api_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        r = json.loads(resp.read())
    if "error" in r:
        raise RuntimeError(r["error"])
    content = r["choices"][0]["message"]["content"]
    issues = [l.strip() for l in content.split("\n") if l.strip() and "通过" not in l]
    status = "PASS" if not issues or "通过" in content else "FAIL"
    return {"status": status, "issues": issues, "raw": content}


def audit_panels(panels_dir, config, rules=None, sleep_seconds=1.5):
    """批量审核画格

    Args:
        panels_dir: Path to panels directory
        config: loaded config dict
        rules: custom audit rules dict (optional, 优先级高于 config 中加载的)

    Returns:
        list of audit result dicts
    """
    panels_dir = Path(panels_dir)
    panels = sorted([p for p in panels_dir.iterdir() if p.suffix == ".png"])

    providers = config.get("providers", {})
    vision_cfg = providers.get("vision", {})
    provider_name = vision_cfg.get("default", "step")
    provider = vision_cfg.get(provider_name, {})

    api_key = get_api_key(config, "vision", provider_name)
    api_url = provider.get("api_url", "https://api.stepfun.com/v1")
    model = provider.get("model", "step-3.7-flash")
    max_tokens = provider.get("max_tokens", 400)
    sleep = provider.get("sleep_seconds", sleep_seconds)

    if not api_key:
        raise ValueError(f"未配置 {provider_name} 的 API key")

    if rules is None:
        # 尝试从 config 加载 rules，fallback 到 DEFAULT
        rules = load_audit_rules(config, panels_dir.parent)

    results = []
    print(f"\n{'='*60}")
    print(f"视觉审核开始 — 共 {len(panels)} 格")
    print(f"{'='*60}\n")

    for i, panel_path in enumerate(panels, 1):
        name = panel_path.stem
        audit_type = classify_panel(name, rules=rules)
        rule = rules.get(audit_type, rules.get("scene", {}))
        prompt = rule.get("prompt", "描述这张图片的内容和问题")
        print(f"  [{i:2d}/{len(panels)}] {name:35s} ({audit_type:12s}) ... ", end="", flush=True)

        try:
            result = audit_panel(panel_path, prompt, api_key, api_url, model, max_tokens)
            result["panel"] = name
            result["type"] = audit_type
            if result["status"] == "PASS":
                print("✅ 通过")
            else:
                print(f"❌ {len(result['issues'])} 个问题")
                for issue in result["issues"]:
                    print(f"      → {issue}")
        except Exception as e:
            result = {"panel": name, "type": audit_type, "status": "ERROR", "issues": [str(e)], "raw": ""}
            print(f"⚠️  ERROR: {str(e)[:50]}")

        results.append(result)
        time.sleep(sleep)

    total = len(results)
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = [r for r in results if r["status"] == "FAIL"]
    errors = [r for r in results if r["status"] == "ERROR"]

    print(f"\n{'='*60}")
    print(f"审核完成！总计 {total} 格 | 通过 {passed} | 问题 {len(failed)} | 错误 {len(errors)}")
    print(f"{'='*60}")

    return results
