"""生成资产存储适配器：默认本地文件，生产可切换 S3/MinIO。"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path, PurePosixPath
from typing import Protocol


class AssetStorage(Protocol):
    mode: str

    def ready(self) -> bool:
        ...

    def put_file(self, path: Path, key: str) -> str:
        ...

    def get_file(self, key: str, destination: Path) -> Path:
        ...


def safe_key(key: str) -> str:
    path = PurePosixPath(key)
    normalized = str(path)
    if normalized in {"", "."} or path.is_absolute() or any(part == ".." for part in path.parts):
        raise ValueError("invalid asset storage key")
    return normalized


class LocalAssetStorage:
    mode = "local"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put_file(self, path: Path, key: str) -> str:
        # 本地 provider 已直接写入 root；返回稳定的 API 相对 URL。
        return f"/assets/{path.name}"

    def ready(self) -> bool:
        return self.root.is_dir()

    def get_file(self, key: str, destination: Path) -> Path:
        source = self.root / Path(key).name
        if not source.exists():
            raise FileNotFoundError(source)
        if source != destination:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
        return destination


class S3AssetStorage:
    mode = "s3"

    def __init__(self, bucket: str, endpoint_url: str | None = None, region: str | None = None, public_base_url: str | None = None) -> None:
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("S3/MinIO storage requires optional boto3; see requirements-storage.txt") from exc
        self.bucket = bucket
        self.endpoint_url = endpoint_url.rstrip("/") if endpoint_url else None
        self.public_base_url = public_base_url.rstrip("/") if public_base_url else None
        self.client = boto3.client("s3", endpoint_url=self.endpoint_url, region_name=region)

    def put_file(self, path: Path, key: str) -> str:
        key = safe_key(key)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.client.upload_file(str(path), self.bucket, key, ExtraArgs={"ContentType": content_type})
        # 媒体通过 API 的归属校验路由读取，不把 bucket 设为 public，也不把
        # endpoint/key 暴露给浏览器。key 仍由业务元数据保存，供 API 进程重启
        # 后按授权回读对象存储。
        return f"/assets/{PurePosixPath(key).name}"

    def ready(self) -> bool:
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return True
        except Exception:
            return False

    def get_file(self, key: str, destination: Path) -> Path:
        key = safe_key(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(destination))
        return destination


def storage_from_env(local_root: str | Path) -> AssetStorage:
    mode = os.environ.get("STUDIO_STORAGE", "local").lower()
    if mode in {"local", "filesystem"}:
        return LocalAssetStorage(local_root)
    if mode in {"s3", "minio"}:
        bucket = os.environ.get("STUDIO_S3_BUCKET")
        if not bucket:
            raise RuntimeError("STUDIO_S3_BUCKET is required when STUDIO_STORAGE=s3")
        return S3AssetStorage(
            bucket,
            os.environ.get("STUDIO_S3_ENDPOINT_URL"),
            os.environ.get("STUDIO_S3_REGION", "us-east-1"),
            os.environ.get("STUDIO_S3_PUBLIC_BASE_URL"),
        )
    raise RuntimeError(f"unsupported STUDIO_STORAGE: {mode}")
