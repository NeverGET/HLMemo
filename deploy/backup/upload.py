#!/usr/bin/env python3
"""Optional S3 upload; compose interpolation environment arrives through stdin, never eval."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

settings = {}
for line in sys.stdin:
    key, sep, value = line.rstrip("\n").partition("=")
    if sep:
        settings[key] = value
bucket = settings.get("S3_BUCKET", "")
if not bucket:
    sys.exit(0)
if not shutil.which("aws"):
    sys.exit("S3_BUCKET is configured but the AWS CLI is not installed; local backup was retained")
environment = os.environ.copy()
for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_DEFAULT_REGION", "AWS_REGION"):
    if settings.get(key):
        environment[key] = settings[key]
prefix = settings.get("S3_PREFIX", "hlmemo").strip("/")
for name in sys.argv[1:]:
    path = Path(name)
    key = "/".join(part for part in (prefix, path.parent.name, path.name) if part)
    cmd = ["aws"]
    if settings.get("S3_ENDPOINT_URL"):
        cmd.extend(["--endpoint-url", settings["S3_ENDPOINT_URL"]])
    cmd.extend(["s3", "cp", str(path), f"s3://{bucket}/{key}", "--only-show-errors"])
    subprocess.run(cmd, env=environment, check=True)
