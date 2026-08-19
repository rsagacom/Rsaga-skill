import unittest
from unittest.mock import patch
import urllib.error

from scripts.edge_runtime_smoke import _get, _smoke_caddyfile, run


class EdgeRuntimeSmokeTests(unittest.TestCase):
    def test_smoke_config_is_derived_from_production_routes_without_placeholders(self):
        source = _smoke_caddyfile(19001, 19002, 19003, 19004)
        self.assertIn("http://127.0.0.1:19004", source)
        self.assertIn("reverse_proxy 127.0.0.1:19001", source)
        self.assertIn("reverse_proxy 127.0.0.1:19002", source)
        self.assertIn(":19003", source)
        self.assertNotIn("{$STUDIO_PUBLIC_HOST}", source)
        self.assertNotIn("api:8787", source)
        self.assertNotIn("web:3000", source)

    def test_unavailable_probe_is_safe_failure_without_transport_details(self):
        with patch("scripts.edge_runtime_smoke.urllib.request.urlopen", side_effect=urllib.error.URLError("secret transport detail")):
            status, body = _get("http://127.0.0.1:1/unavailable")
        self.assertEqual(status, 0)
        self.assertEqual(body, "")

    def test_missing_caddy_is_skipped_or_required_failure(self):
        with patch("scripts.edge_runtime_smoke.shutil.which", return_value=None):
            self.assertEqual(run(), {"status": "skipped", "reason": "caddy-not-found", "required": False})
            self.assertEqual(run(require_caddy=True), {"status": "failed", "reason": "caddy-not-found", "required": True})


if __name__ == "__main__":
    unittest.main()
