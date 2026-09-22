#!/usr/bin/env python3
"""Optional S3 upload; isolated backup.env settings arrive through stdin, never eval."""
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
# Credentials come only from backup.env, never inherited shell/profile settings.
environment = {key: value for key, value in os.environ.items() if not key.startswith("AWS_")}
environment.update(AWS_CONFIG_FILE=os.devnull, AWS_SHARED_CREDENTIALS_FILE=os.devnull,
                   AWS_EC2_METADATA_DISABLED="true")
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
    try:
        subprocess.run(cmd, env=environment, check=True)
    except subprocess.CalledProcessError as error:
        sys.exit(f"S3 upload failed (exit {error.returncode}); local backup retained")
