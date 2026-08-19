"""执行 AI 漫剧工作台的 PostgreSQL 迁移。

Compose 的 ``docker-entrypoint-initdb.d`` 只会在新建数据卷时执行。生产栈
因此单独运行这个模块，让已有 PostgreSQL 卷也能在 API 启动前按序补齐迁移。
迁移文件本身保持可重复执行；本命令不打印 DSN 或其它环境变量值。
"""

from __future__ import annotations

import argparse
import os
import sys

from .store import PostgresStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("STUDIO_DATABASE_URL", ""),
        help="PostgreSQL DSN；默认读取 STUDIO_DATABASE_URL，不会打印其值",
    )
    parser.add_argument("--runtime-dir", default=os.environ.get("STUDIO_RUNTIME_DIR", "/app/runtime"))
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("STUDIO_DATABASE_URL or --database-url is required")
    PostgresStore(args.database_url, args.runtime_dir).initialize()
    print("postgres migrations applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
