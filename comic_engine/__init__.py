"""小说转漫画引擎 (novel-to-comic-engine)

将中文长篇小说章节转换为东亚风格黑白漫画（水墨、B5判、含对话框/旁白/SFX）。

核心模块:
- config: 配置加载（支持 PyYAML 及简易 fallback）
- generator: 画格批量生成（StepFun API）
- auditor: 多模态视觉审核（Step/Kimi vision）
- layout: ComicLayoutEngine 排版引擎（PIL + ReportLab）
- utils: 通用工具（base64、storyboard 加载、跨平台字体检测）

公共 API:
    from comic_engine.config import load_config
    from comic_engine.generator import generate_panels, estimate_resources
    from comic_engine.auditor import audit_panels, load_audit_rules
    from comic_engine.layout import ComicLayoutEngine
    from comic_engine.utils import load_storyboard, detect_platform_fonts

版本: 0.2.0
"""

from .config import load_config
from .generator import generate_panels, estimate_resources
from .auditor import audit_panels, load_audit_rules
from .layout import ComicLayoutEngine
from .utils import load_storyboard, detect_platform_fonts

__version__ = "0.3.0"
__all__ = [
    "load_config",
    "generate_panels",
    "estimate_resources",
    "audit_panels",
    "load_audit_rules",
    "ComicLayoutEngine",
    "load_storyboard",
    "detect_platform_fonts",
]
