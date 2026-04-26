import asyncio

import boto3
from botocore.config import Config

from app.core.config import settings

_BOTO_CONFIG = Config(
    connect_timeout=5,
    read_timeout=10,
    signature_version="s3v4",
    s3={"addressing_style": "virtual"},
)


def _s3_client():
    return boto3.client(
        "s3",
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=_BOTO_CONFIG,
    )


def presign_get(key: str | None, expires: int = 3600) -> str | None:
    if not key:
        return None
    s3 = _s3_client()
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket_name.strip(), "Key": key},
        ExpiresIn=expires,
    )


async def presign_put(key: str, content_type: str, expires: int = 300) -> str:
    def _generate() -> str:
        s3 = _s3_client()
        return s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": settings.s3_bucket_name.strip(),
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=expires,
        )

    return await asyncio.to_thread(_generate)
