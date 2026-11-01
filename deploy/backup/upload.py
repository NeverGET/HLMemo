#!/usr/bin/env python3
"""Optional S3 and rclone uploads; isolated backup.env settings arrive through stdin, never eval.

``RCLONE_REMOTE`` (for example an encrypted ``crypt`` remote over Google Drive) with ``RCLONE_CONFIG``
copies each dump to ``<remote>/<tier>/<name>``; then dumps older than ``RCLONE_KEEP_DAYS`` (default 14)
are deleted from the remote's daily tier and older than ``RCLONE_KEEP_WEEKLY_DAYS`` (default 70) from
its weekly tier. The remote's other tiers are never pruned. A failure exits non-zero after every target
was tried; the local backup is retained either way.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

settings = {}
for line in sys.stdin:
    key, sep, value = line.rstrip("\n").partition("=")
    if sep:
        settings[key] = value
failures: list[str] = []


def rclone_upload(paths: list[Path]) -> None:
    remote = settings.get("RCLONE_REMOTE", "").rstrip("/")
    if not remote:
        return
    if not shutil.which("rclone"):
        failures.append("RCLONE_REMOTE is configured but rclone is not installed")
        return
    base = ["rclone", "--config", settings.get("RCLONE_CONFIG", "/etc/hlmemo/rclone.conf"), "-q"]
    # rclone reads only its own config file; no inherited RCLONE_* settings
    environment = {key: value for key, value in os.environ.items() if not key.startswith("RCLONE_")}
    tiers = set()
    for path in paths:
        tiers.add(path.parent.name)
        try:
            subprocess.run(
                [*base, "copyto", str(path), f"{remote}/{path.parent.name}/{path.name}"],
                env=environment,
                check=True,
            )
        except subprocess.CalledProcessError as error:
            failures.append(f"rclone upload of {path.name} failed (exit {error.returncode})")
            return
    keep = {
        "daily": settings.get("RCLONE_KEEP_DAYS") or "14",
        "weekly": settings.get("RCLONE_KEEP_WEEKLY_DAYS") or "70",
    }
    for tier in sorted(tiers & set(keep)):
        cmd = [*base, "delete", f"{remote}/{tier}", "--min-age", f"{int(keep[tier])}d", "--include", "*.dump"]
        try:
            subprocess.run(cmd, env=environment, check=True)
        except subprocess.CalledProcessError as error:
            failures.append(f"rclone prune of {tier} failed (exit {error.returncode})")


rclone_upload([Path(name) for name in sys.argv[1:]])
bucket = settings.get("S3_BUCKET", "")
if not bucket:
    sys.exit("; ".join(failures) + "; local backup retained" if failures else 0)
if not shutil.which("aws"):
    sys.exit("S3_BUCKET is configured but the AWS CLI is not installed; local backup was retained")
# Credentials come only from backup.env, never inherited shell/profile settings.
environment = {key: value for key, value in os.environ.items() if not key.startswith("AWS_")}
environment.update(
    AWS_CONFIG_FILE=os.devnull, AWS_SHARED_CREDENTIALS_FILE=os.devnull, AWS_EC2_METADATA_DISABLED="true"
)
for key in (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_DEFAULT_REGION",
    "AWS_REGION",
):
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
        failures.append(f"S3 upload failed (exit {error.returncode})")
        break
if failures:
    sys.exit("; ".join(failures) + "; local backup retained")
