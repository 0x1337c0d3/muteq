import logging
from typing import Optional


def upload_dashboard(
    html_content: str,
    db_path: str,
    bucket: str,
    aws_region: str,
    aws_access_key_id: Optional[str],
    aws_secret_access_key: Optional[str],
    logger: logging.Logger,
) -> bool:
    """
    Upload index.html and a backup of the SQLite database to the S3 bucket.

    If aws_access_key_id is None, boto3 uses the default credential chain
    (~/.aws/credentials, environment variables, instance profile).

    Returns True if the HTML upload succeeded (DB backup failure is logged but
    does not affect the return value).
    """
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        logger.error("[S3] boto3 is not installed. Run: pip install boto3")
        return False

    kwargs = {"region_name": aws_region}
    if aws_access_key_id and aws_secret_access_key:
        kwargs["aws_access_key_id"] = aws_access_key_id
        kwargs["aws_secret_access_key"] = aws_secret_access_key

    s3 = boto3.client("s3", **kwargs)

    # ── Upload dashboard HTML ─────────────────────────────────────────────────
    try:
        s3.put_object(
            Bucket=bucket,
            Key="index.html",
            Body=html_content.encode("utf-8"),
            ContentType="text/html; charset=utf-8",
            # no-cache ensures CloudFront always fetches fresh content from S3
            CacheControl="no-cache, max-age=0",
        )
    except (BotoCoreError, ClientError) as exc:
        logger.error(f"[S3] Dashboard upload failed: {exc}")
        return False
    except Exception as exc:
        logger.error(f"[S3] Unexpected error uploading dashboard: {exc}")
        return False

    # ── Upload SQLite DB backup ───────────────────────────────────────────────
    # try:
    #     with open(db_path, "rb") as f:
    #         s3.put_object(
    #             Bucket=bucket,
    #             Key="backup/muteq.db",
    #             Body=f,
    #             ContentType="application/octet-stream",
    #         )
    #     logger.info(f"[S3] DB backup uploaded to s3://{bucket}/backup/muteq.db")
    # except FileNotFoundError:
    #     logger.warning(f"[S3] DB file not found at {db_path}, skipping backup.")
    # except (BotoCoreError, ClientError) as exc:
    #     logger.warning(f"[S3] DB backup failed: {exc}")
    # except Exception as exc:
    #     logger.warning(f"[S3] Unexpected error uploading DB backup: {exc}")

    return True
