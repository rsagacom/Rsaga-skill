"""视觉审核模块"""
import json
import time
import urllib.request
from pathlib import Path

from .config import get_api_key
from .utils import encode_image_to_base64


# 默认审核规则库
DEFAULT_AUDIT_RULES = {
    "qiyuan": {
        "keywords": ["祁思远", "主角", "男人", "青年", "男子", "地铁", "背包",
                     "恐惧", "麻木", "习惯", "暴君", "一周", "三天", "两天",
                     "吃饱", "意识", "噩梦", "记不住", "锁定", "丝线", "满足",
                     "新闻", "强奸", "虐杀", "诈骗", "毒贩", "正义", "便利店",
                     "手机", "两张脸", "锁定他们", "眼光", "罪孽", "走得慢",
                     "寒意", "少管所", "捂住", "倒在", "喊不", "三分钟", "咽气",
                     "法医", "半年", "两百", "行刑者", "城中村", "电脑", "论坛",
                     "12月", "呼唤", "死寂", "存在感", "勾勒", "等了一", "恐慌",
                     "困惑", "庆幸", "投简历", "小公司", "地铁", "领导", "站着",
                     "回来了", "留白"],
        "prompt": """审核要求：检查图中男子是否符合角色设定。
角色设定：Young Chinese man 25yo, thin black-frame glasses, short black hair with bangs covering forehead, beige casual blazer, dark blue V-neck shirt, old black backpack, tired hollow eyes with dark circles, pale skin.

请逐一检查：
1. 发型是否为黑色短发带刘海？
2. 是否佩戴细黑框眼镜？
3. 服装是否为米色休闲西装+深蓝色V领衬衫？
4. 画面是否出现重影/叠加/双重人像？

只输出发现的问题，每条一行，格式：「问题类型：具体描述」。如无问题，输出「通过」。"""
    },
    "supernatural": {
        "keywords": ["贪婪", "吃饱", "意识", "眼光", "罪孽", "走得慢", "租客", "房东", "实体"],
        "prompt": """审核要求：检查超自然实体。

1. 是否只有发光眼窝，无完整人脸？
2. 是否有嘴巴/鼻子等面部细节？
3. 身形是否半透明/烟雾质感？
4. 是否出现重影/叠加？

只输出发现的问题。如无问题，输出「通过」。"""
    },
    "hand": {
        "keywords": ["手", "手指", "攥", "手掌"],
        "prompt": """审核要求：检查手部绘制质量。

1. 手指数量是否为5根？
2. 手掌轮廓是否正常？
3. 是否过度写实到不自然？

只输出发现的问题。如无问题，输出「通过」。"""
    },
    "scene": {
        "keywords": ["扉页", "少管所", "法医", "城中村", "电脑", "论坛", "12月", "地铁", "领导", "留白"],
        "prompt": """审核要求：检查场景合规。

1. 是否有不应出现的儿童？
2. 空旷场景是否有无关人群？
3. 是否出现与剧情时代不符的元素？

只输出发现的问题。如无问题，输出「通过」。"""
    },
    "abstract": {
        "keywords": ["抽象", "暴君", "吃饱", "意识", "两百", "留白", "记不住"],
        "prompt": """审核要求：检查抽象/隐喻画面。

1. 是否出现人物重影/叠加？
2. 画面是否有撕裂/扭曲？
3. 超自然实体是否出现完整人脸？

只输出发现的问题。如无问题，输出「通过」。"""
    }
}


def classify_panel(name, rules=None):
    """根据文件名分类画格审核类型"""
    if rules is None:
        rules = DEFAULT_AUDIT_RULES
    for audit_type, rule in rules.items():
        for kw in rule["keywords"]:
            if kw in name:
                return audit_type
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
        rules: custom audit rules dict (optional)

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
        rules = DEFAULT_AUDIT_RULES

    results = []
    print(f"\n{'='*60}")
    print(f"视觉审核开始 — 共 {len(panels)} 格")
    print(f"{'='*60}\n")

    for i, panel_path in enumerate(panels, 1):
        name = panel_path.stem
        audit_type = classify_panel(name, rules)
        prompt = rules[audit_type]["prompt"]
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
