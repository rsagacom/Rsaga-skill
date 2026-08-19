import unittest
from pathlib import Path


WEB_SOURCE = Path(__file__).resolve().parents[1].joinpath("web", "app", "page.tsx").read_text(encoding="utf-8")


class WebCharacterReferenceContractTests(unittest.TestCase):
    def test_workbench_exposes_user_reference_upload_for_all_three_views(self):
        self.assertIn("/reference-upload", WEB_SOURCE)
        self.assertIn('accept="image/png,image/jpeg,image/webp"', WEB_SOURCE)
        self.assertIn('view,', WEB_SOURCE)
        self.assertIn('view === "front" ? "上传正面"', WEB_SOURCE)
        self.assertIn('view === "side" ? "上传侧面"', WEB_SOURCE)
        self.assertIn('上传背面', WEB_SOURCE)

    def test_upload_stays_in_the_existing_free_reference_path(self):
        self.assertIn("async function uploadCharacterReference", WEB_SOURCE)
        self.assertIn("角色参考图不能超过 16 MB", WEB_SOURCE)
        self.assertNotIn("character-reference-upload-cost", WEB_SOURCE)


if __name__ == "__main__":
    unittest.main()
