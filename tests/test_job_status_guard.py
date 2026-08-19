from pathlib import Path
import tempfile
import unittest

from studio_api.service import StudioService, new_id, now
from studio_api.store import StudioStore


class JobStatusGuardTests(unittest.TestCase):
    def test_sqlite_rejects_terminal_job_reopening(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = StudioService(StudioStore(root / "studio.sqlite3"), root / "assets")
            with service.store.connection() as connection:
                job_id = new_id("job")
                timestamp = now()
                connection.execute(
                    "INSERT INTO jobs(id, user_id, kind, target_id, status, cost_credits, provider, created_at, updated_at) VALUES (?, ?, 'text', 'target', 'queued', 0, 'local', ?, ?)",
                    (job_id, "local-user", timestamp, timestamp),
                )
                connection.execute("UPDATE jobs SET status = 'running', updated_at = ? WHERE id = ?", (now(), job_id))
                connection.execute("UPDATE jobs SET status = 'completed', updated_at = ? WHERE id = ?", (now(), job_id))
                with self.assertRaisesRegex(Exception, "invalid job status transition"):
                    connection.execute("UPDATE jobs SET status = 'queued', updated_at = ? WHERE id = ?", (now(), job_id))


if __name__ == "__main__":
    unittest.main()
