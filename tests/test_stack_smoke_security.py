import io
import json
import os
import urllib.error
import unittest
from unittest.mock import patch

from scripts.stack_smoke import SmokeClient, get_json_url, get_url, wait_for_job


class StackSmokeSecurityTests(unittest.TestCase):
    def http_error(self, url: str = "https://smoke.example.test/api") -> urllib.error.HTTPError:
        return urllib.error.HTTPError(
            url,
            502,
            "upstream failure",
            None,
            io.BytesIO(json.dumps({"error": "provider-secret-response"}).encode("utf-8")),
        )

    def test_client_http_failures_do_not_echo_response_bodies(self):
        cases = [
            ("call", lambda client: client.call("/api/projects")),
            ("download", lambda client: client.download("/assets/private.mp4")),
            ("upload", lambda client: client.upload_bytes("/api/upload", b"data", "application/octet-stream")),
        ]
        for name, operation in cases:
            with self.subTest(operation=name), patch(
                "scripts.stack_smoke.urllib.request.urlopen", side_effect=self.http_error()
            ):
                with self.assertRaises(RuntimeError) as failure:
                    operation(SmokeClient("https://smoke.example.test", "token"))
                self.assertIn("returned HTTP 502", str(failure.exception))
                self.assertNotIn("provider-secret-response", str(failure.exception))

    def test_internal_job_failure_does_not_echo_api_error(self):
        with patch.dict(os.environ, {"STUDIO_SMOKE_INTERNAL_TOKEN": "smoke-internal-token"}), patch(
            "scripts.stack_smoke.urllib.request.urlopen", side_effect=self.http_error()
        ):
            with self.assertRaises(RuntimeError) as failure:
                SmokeClient("https://smoke.example.test").run_local_job("job-1")
        self.assertIn("returned HTTP 502", str(failure.exception))
        self.assertNotIn("provider-secret-response", str(failure.exception))

    def test_failed_job_does_not_echo_raw_job_error(self):
        class FailedJobClient:
            internal_token = ""

            def call(self, path):
                return {"status": "failed", "error": "provider-secret-response"}

        with self.assertRaises(RuntimeError) as failure:
            wait_for_job(FailedJobClient(), "job-1", timeout=1)
        self.assertIn("job-1 ended failed", str(failure.exception))
        self.assertNotIn("provider-secret-response", str(failure.exception))

    def test_public_probe_failures_do_not_echo_url_or_response_body(self):
        with patch(
            "scripts.stack_smoke.urllib.request.urlopen", side_effect=self.http_error("https://user:secret@smoke.example.test/health")
        ):
            with self.assertRaises(RuntimeError) as url_failure:
                get_url("https://user:secret@smoke.example.test/health")
            with self.assertRaises(RuntimeError) as json_failure:
                get_json_url("https://user:secret@smoke.example.test/health")
        for failure in (url_failure, json_failure):
            self.assertNotIn("user:secret", str(failure.exception))
            self.assertNotIn("provider-secret-response", str(failure.exception))


if __name__ == "__main__":
    unittest.main()
