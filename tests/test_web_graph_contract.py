import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "web" / "app" / "board" / "[projectId]" / "page.tsx"


class WebGraphContractTests(unittest.TestCase):
    def test_graph_canvas_exposes_status_and_type_filters(self):
        source = BOARD.read_text(encoding="utf-8")
        for marker in (
            "StatusFilter",
            "TypeFilter",
            "缺失 / 待处理",
            "已就绪",
            "故事资产",
            "制作链路",
            "媒体资产",
            "visibleIds",
            "board-empty",
        ):
            self.assertIn(marker, source)

    def test_graph_canvas_filters_edges_to_visible_nodes(self):
        source = BOARD.read_text(encoding="utf-8")
        self.assertIn("visibleIds.has(edge.source) && visibleIds.has(edge.target)", source)


if __name__ == "__main__":
    unittest.main()
