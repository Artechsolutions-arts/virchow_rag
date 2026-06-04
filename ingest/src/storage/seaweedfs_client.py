"""
SeaweedFS Object Storage Client (S3-Compatible)
Handles file upload, download, deletion, and URL generation via S3 API.
Integrates with the RAG pipeline for storing raw PDFs and processed chunks.
"""

import io
import logging
import mimetypes
import os
from pathlib import Path
from typing import Optional

import aioboto3
from botocore.exceptions import ClientError

# Import config
try:
    from src.config import CFG as settings
except ImportError:
    from src.config import cfg as settings

logger = logging.getLogger(__name__)


class SeaweedFSClient:
    """
    Client for SeaweedFS object storage using the S3-compatible interface.
    
    This client uses aioboto3 for asynchronous S3 operations.
    """

    def __init__(
        self,
        endpoint_url: str = None,
        aws_access_key_id: str = None,
        aws_secret_access_key: str = None,
        bucket: str = None,
        region_name: str = "us-east-1",
    ):
        self.endpoint_url = (endpoint_url or getattr(settings, "SEAWEEDFS_S3_ENDPOINT", "http://192.168.10.10:8333"))
        self.access_key = (aws_access_key_id or getattr(settings, "SEAWEEDFS_ACCESS_KEY", "any"))
        self.secret_key = (aws_secret_access_key or getattr(settings, "SEAWEEDFS_SECRET_KEY", "any"))
        self.bucket = (bucket or getattr(settings, "SEAWEEDFS_BUCKET", "rag-docs"))
        self.region_name = region_name
        
        self.session = aioboto3.Session()

    async def _ensure_bucket_exists(self, s3_client):
        """Internal helper to create bucket if it doesn't exist."""
        try:
            await s3_client.head_bucket(Bucket=self.bucket)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code == "404":
                logger.info(f"Creating bucket: {self.bucket}")
                await s3_client.create_bucket(Bucket=self.bucket)
            else:
                logger.error(f"Error checking bucket {self.bucket}: {e}")

    async def _upload_via_filer(
        self,
        object_key: str,
        data: bytes,
        content_type: str,
    ) -> str:
        """Upload bytes directly to SeaweedFS Filer HTTP API (fallback when S3 is down)."""
        import aiohttp
        filer_base = getattr(settings, "SEAWEEDFS_FILER_URL", "http://192.168.10.10:8889").rstrip("/")
        url = f"{filer_base}/buckets/{self.bucket}/{object_key.lstrip('/')}"
        async with aiohttp.ClientSession() as session:
            async with session.put(
                url, data=data,
                headers={"Content-Type": content_type},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    raise RuntimeError(f"Filer PUT {resp.status}: {body[:200]}")
        logger.info("Uploaded %s to bucket %s via Filer", object_key, self.bucket)
        return object_key

    async def upload_file(
        self,
        object_key: str,
        data: bytes | io.IOBase,
        content_type: str = None,
        metadata: dict = None,
    ) -> str:
        """Upload bytes or a file-like object to SeaweedFS.

        Tries S3 API first; falls back to Filer HTTP API automatically
        so uploads succeed even when the S3 gateway (port 8333) is down.
        """
        if content_type is None:
            guessed, _ = mimetypes.guess_type(object_key)
            content_type = guessed or "application/octet-stream"

        if isinstance(data, (bytes, bytearray)):
            raw_bytes = bytes(data)
            file_obj = io.BytesIO(raw_bytes)
        else:
            raw_bytes = data.read()
            file_obj = io.BytesIO(raw_bytes)

        extra_args = {"ContentType": content_type}
        if metadata:
            extra_args["Metadata"] = {k: str(v) for k, v in metadata.items()}

        # Try S3 first
        try:
            async with self.session.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region_name,
            ) as s3:
                await self._ensure_bucket_exists(s3)
                await s3.upload_fileobj(file_obj, self.bucket, object_key, ExtraArgs=extra_args)
                logger.info("Uploaded %s to bucket %s via S3", object_key, self.bucket)
                return object_key
        except Exception as s3_exc:
            logger.warning(
                "S3 upload failed for '%s' (%s) — retrying via Filer HTTP API",
                object_key, s3_exc,
            )

        # Filer fallback
        try:
            return await self._upload_via_filer(object_key, raw_bytes, content_type)
        except Exception as filer_exc:
            msg = f"SeaweedFS upload failed for '{object_key}' — S3: {s3_exc} | Filer: {filer_exc}"
            logger.error(msg)
            raise RuntimeError(msg) from filer_exc

    async def upload_local_file(self, object_key: str, local_path: str | Path) -> str:
        """Upload a local file to SeaweedFS via S3."""
        local_path = Path(local_path)
        if not local_path.exists():
            raise FileNotFoundError(f"Local file not found: {local_path}")

        content_type, _ = mimetypes.guess_type(str(local_path))
        with open(local_path, "rb") as fh:
            return await self.upload_file(object_key, fh, content_type=content_type)

    async def download_file(self, object_key: str) -> bytes:
        """Download an object from SeaweedFS via S3."""
        try:
            async with self.session.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region_name,
            ) as s3:
                response = await s3.get_object(Bucket=self.bucket, Key=object_key)
                async with response["Body"] as stream:
                    return await stream.read()
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "NoSuchKey":
                raise FileNotFoundError(f"Object not found in S3: '{object_key}'") from e
            raise RuntimeError(f"SeaweedFS (S3) download failed: {e}") from e
        except Exception as exc:
            raise RuntimeError(f"SeaweedFS (S3) connection error during download: {exc}") from exc

    async def delete_file(self, object_key: str) -> bool:
        """Delete an object from SeaweedFS via S3."""
        try:
            async with self.session.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region_name,
            ) as s3:
                await s3.delete_object(Bucket=self.bucket, Key=object_key)
                return True
        except Exception as exc:
            logger.error(f"SeaweedFS (S3) delete failed: {exc}")
            return False

    async def list_files(self, prefix: str = "") -> list[dict]:
        """List objects under a prefix via S3."""
        try:
            async with self.session.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region_name,
            ) as s3:
                paginator = s3.get_paginator("list_objects_v2")
                pages = paginator.paginate(Bucket=self.bucket, Prefix=prefix)
                
                results = []
                async for page in pages:
                    for obj in page.get("Contents", []):
                        results.append({
                            "name": obj["Key"],
                            "size": obj["Size"],
                            "modified": obj["LastModified"].isoformat(),
                        })
                return results
        except Exception as exc:
            logger.warning("SeaweedFS (S3) list failed for prefix '%s': %s", prefix, exc)
            return []

    async def list_job_files(self, job_id: str) -> list[dict]:
        """List all files in raw/ and processed/ directories."""
        results = []
        for p in ["raw/", "processed/"]:
            files = await self.list_files(prefix=p)
            results.extend(files)
        return results

    async def delete_job_artefacts(self, job_id: str) -> int:
        """Delete all objects in raw/ and processed/ directories."""
        files = await self.list_job_files(job_id)
        count = 0
        for f in files:
            if await self.delete_file(f["name"]):
                count += 1
        return count

    def pdf_url(self, filename: str) -> str:
        """Return the public Filer URL for a specific raw PDF."""
        key = f"raw/{Path(filename).name}"
        return self.public_url(key)

    def public_url(self, object_key: str) -> str:
        """Return the public Filer URL for a given object key."""
        filer_url = getattr(settings, "SEAWEEDFS_FILER_URL", "http://192.168.10.10:8889").rstrip("/")
        return f"{filer_url}/{self.bucket}/{object_key.lstrip('/')}"

    async def health_check(self) -> bool:
        """Verify connectivity by listing buckets."""
        try:
            async with self.session.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region_name,
            ) as s3:
                await s3.list_buckets()
                return True
        except Exception:
            return False

    async def health(self) -> dict:
        """Return health status as expected by routes.py."""
        is_up = await self.health_check()
        return {"seaweedfs": "online" if is_up else "offline"}

    async def close(self):
        pass


def raw_pdf_key(filename: str) -> str:
    return f"raw/{Path(filename).name}"

def processed_key(filename: str) -> str:
    return f"processed/{Path(filename).stem}.json"

def chunk_key(job_id: str, chunk_index: int) -> str:
    return f"chunks/{job_id}/chunk_{chunk_index:05d}.json"
