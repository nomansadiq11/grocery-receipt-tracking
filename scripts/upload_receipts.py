"""Upload local receipts/raw files to the private S3 input prefix."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import boto3

SUPPORTED = {".pdf", ".jpg", ".jpeg", ".png"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default="receipts/raw")
    args = parser.parse_args()
    bucket = os.environ["GROCERY_BUCKET"]
    prefix = os.getenv("RAW_PREFIX", "receipts/raw/").rstrip("/") + "/"
    client = boto3.Session(profile_name=os.getenv("AWS_PROFILE") or None, region_name=os.getenv("AWS_REGION")).client("s3")

    files = sorted(path for path in Path(args.directory).rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED)
    if not files:
        print(f"No PDF/JPG/JPEG/PNG files found in {args.directory}")
        return
    for path in files:
        key = prefix + path.name
        client.upload_file(str(path), bucket, key)
        print(f"Uploaded {path} -> s3://{bucket}/{key}")


if __name__ == "__main__":
    main()
