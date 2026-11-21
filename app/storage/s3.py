"""S3-compatible artifact storage backed by boto3."""

from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


class S3Storage:
    """S3 adapter compatible with local S3 implementations."""

    def __init__(
        self, endpoint_url: str, access_key: str, secret_key: str, bucket: str, region: str
    ) -> None:
        self.bucket = bucket
        self.client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(s3={"addressing_style": "path"}),
        )
        try:
            self.client.head_bucket(Bucket=bucket)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code not in {"404", "NoSuchBucket", "NotFound"}:
                raise
            self.client.create_bucket(Bucket=bucket)

    def save(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def load(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return bytes(response["Body"].read())

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def prune_expired(self, ttl_seconds: int) -> int:
        """Delete expired objects from the configured bucket."""
        cutoff = datetime.now(UTC) - timedelta(seconds=ttl_seconds)
        paginator = self.client.get_paginator("list_objects_v2")
        removed = 0
        for page in paginator.paginate(Bucket=self.bucket):
            for item in page.get("Contents", []):
                if item["LastModified"] < cutoff:
                    self.delete(item["Key"])
                    removed += 1
        return removed
