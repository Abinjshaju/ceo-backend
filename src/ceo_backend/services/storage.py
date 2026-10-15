"""Private object storage for original documents. Clients never see storage keys;
they get short-lived signed URLs."""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Protocol

from ceo_backend.config import Settings

API_PREFIX = "/api"
from ceo_backend.errors import Invalid, NotFound, Unauthorized


class Storage(Protocol):
    def upload_target(self, key: str, content_type: str, max_bytes: int) -> dict: ...

    def download_url(self, key: str, filename: str) -> str: ...

    def size(self, key: str) -> int | None: ...

    def read(self, key: str) -> bytes: ...

    def write(self, key: str, data: bytes) -> None: ...

    def delete(self, key: str) -> None: ...


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class LocalStorage:
    """Files on local disk, reachable only through HMAC-signed, expiring URLs served by
    this API. Intended for development and single-host deployments."""

    def __init__(self, settings: Settings) -> None:
        self.root = Path(settings.storage_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.base_url = settings.public_base_url
        self.ttl = settings.upload_url_ttl_seconds
        # Without a configured secret, URLs stop working when the process restarts.
        self._secret = (settings.storage_signing_secret or secrets.token_hex(32)).encode()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise Invalid("Invalid storage key.")
        return path

    def sign(self, payload: dict) -> str:
        body = _b64(json.dumps(payload, separators=(",", ":")).encode())
        mac = _b64(hmac.new(self._secret, body.encode(), hashlib.sha256).digest())
        return f"{body}.{mac}"

    def verify(self, token: str, operation: str) -> dict:
        try:
            body, mac = token.split(".", 1)
            expected = _b64(hmac.new(self._secret, body.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(mac, expected):
                raise ValueError
            payload = json.loads(_unb64(body))
        except Exception as exc:
            raise Unauthorized("Invalid storage link.") from exc
        if payload.get("op") != operation or payload.get("exp", 0) < time.time():
            raise Unauthorized("Storage link expired or not valid for this operation.")
        return payload

    def upload_target(self, key: str, content_type: str, max_bytes: int) -> dict:
        expires = int(time.time()) + self.ttl
        token = self.sign({"op": "put", "k": key, "ct": content_type, "max": max_bytes, "exp": expires})
        return {
            "url": f"{self.base_url}{API_PREFIX}/storage/objects/{token}",
            "method": "PUT",
            "headers": {"Content-Type": content_type},
            "expires_at": expires,
        }

    def download_url(self, key: str, filename: str) -> str:
        expires = int(time.time()) + self.ttl
        token = self.sign({"op": "get", "k": key, "fn": filename, "exp": expires})
        return f"{self.base_url}{API_PREFIX}/storage/objects/{token}"

    def write(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    def size(self, key: str) -> int | None:
        path = self._path(key)
        return path.stat().st_size if path.exists() else None

    def read(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise NotFound("Stored file not found.")
        return path.read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class S3Storage:
    """Private S3 (or S3-compatible) bucket with presigned URLs."""

    def __init__(self, settings: Settings) -> None:
        import boto3

        if not settings.s3_bucket:
            raise RuntimeError("STORAGE_BACKEND=s3 needs S3_BUCKET.")
        self.bucket = settings.s3_bucket
        self.ttl = settings.upload_url_ttl_seconds
        self.client = boto3.client(
            "s3", region_name=settings.s3_region, endpoint_url=settings.s3_endpoint_url
        )

    def upload_target(self, key: str, content_type: str, max_bytes: int) -> dict:
        url = self.client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=self.ttl,
        )
        return {
            "url": url,
            "method": "PUT",
            "headers": {"Content-Type": content_type},
            "expires_at": int(time.time()) + self.ttl,
        }

    def download_url(self, key: str, filename: str) -> str:
        safe = filename.replace('"', "")
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{safe}"',
            },
            ExpiresIn=self.ttl,
        )

    def size(self, key: str) -> int | None:
        from botocore.exceptions import ClientError

        try:
            return self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"]
        except ClientError:
            return None

    def read(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def write(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


def make_storage(settings: Settings) -> Storage:
    if settings.storage_backend == "s3":
        return S3Storage(settings)
    return LocalStorage(settings)
