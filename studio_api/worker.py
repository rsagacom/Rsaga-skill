"""SQLite 持久化 Job worker。

生产环境可把同一批 job payload 投递到 BullMQ；本 worker 用来保证单机、无
Redis 环境也能恢复 queued 状态，并验证 API/账本的异步语义。
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from .service import StudioService
from .store import StudioStore


class LocalJobWorker:
    def __init__(self, runtime_dir: str | Path) -> None:
        runtime = Path(runtime_dir)
        self.service = StudioService(StudioStore(runtime / "studio.sqlite3"), runtime / "assets")

    def process_once(self, limit: int = 10) -> int:
        self.service.recover_stale_jobs()
        rows = self.service.store.all("SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT ?", (limit,))
        for row in rows:
            self.service.run_job(row["id"])
        return len(rows)

    def run(self, interval: float = 1.0) -> None:
        while True:
            self.process_once()
            time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 漫剧本地 Job worker")
    parser.add_argument("--runtime-dir", default="runtime")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()
    worker = LocalJobWorker(args.runtime_dir)
    if args.once:
        print(worker.process_once())
    else:
        worker.run(args.interval)


if __name__ == "__main__":
    main()
