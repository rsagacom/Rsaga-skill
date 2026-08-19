import unittest
from pathlib import Path


class WebSecurityContractTests(unittest.TestCase):
    def test_account_security_lists_and_revokes_server_sessions(self):
        source = (Path(__file__).parents[1] / "web" / "app" / "page.tsx").read_text(encoding="utf-8")
        for marker in (
            '"/api/auth/sessions"',
            "撤销设备",
            "当前设备",
            "session.id",
            "last_seen_at",
            "原始 User-Agent",
        ):
            self.assertIn(marker, source)


if __name__ == "__main__":
    unittest.main()
