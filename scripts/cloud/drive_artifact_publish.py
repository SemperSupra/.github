#!/usr/bin/env python3
"""Trusted-host Drive artifact publisher via preconfigured rclone OAuth remote.

No Google secrets/ADC material in GHA; public GHA may *plan* only.
Payloads are source-verified, uploaded immutably, then downloaded and rehashed
before a receipt can claim remote acceptance. Never uses a shell.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

try:
    from .artifact_manifest import verify
except ImportError:  # direct CLI script invocation
    from artifact_manifest import verify

REMOTE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,31}:[A-Za-z0-9_ .()/-]{1,240}$")
TIMEOUT = 900
MAX_UPLOAD_BYTES = 2 * 1024**3
MAX_FILES = 100

def sanitized_remote_prefix(raw: str) -> str:
    if not REMOTE.fullmatch(raw) or "//" in raw or any(x in raw.split(":")[1].split("/") for x in (".", "..")):
        raise ValueError("REMOTE_ROOT_INVALID")
    return raw.rstrip("/")

def selection(manifest: dict) -> list[dict]:
    chosen = [row for row in manifest["files"] if row["recommended_storage"] == "drive"]
    if len(chosen) > MAX_FILES or sum(row["size_bytes"] for row in chosen) > MAX_UPLOAD_BYTES:
        raise ValueError("PILOT_UPLOAD_BUDGET_EXCEEDED")
    return chosen

def remote_for(remote_root: str, manifest: dict, rel: str) -> str:
    return f"{remote_root}/{manifest['run_id']}/{rel}"

def invoke(args, *, out=None):
    try:
        run = subprocess.run(args, stdin=subprocess.DEVNULL,
                             stdout=out if out is not None else subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, timeout=TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("RCLONE_TRANSPORT_UNKNOWN")
    if run.returncode != 0:
        raise ValueError("RCLONE_REMOTE_OPERATION_FAILED")

def upload_and_verify(root: Path, manifest: dict, remote_root: str, rows: list[dict]) -> list[dict]:
    accepted = []
    for row in rows:
        remote = remote_for(remote_root, manifest, row["relative_path"])
        local = root / row["relative_path"]
        # Immutable uploads: existing remote name is a conflict, not overwrite.
        invoke(["rclone","copyto","--immutable","--retries","1",
                "--low-level-retries","1",str(local),remote])
        with tempfile.TemporaryFile(mode="w+b") as readback:
            invoke(["rclone","cat",remote],out=readback)
            readback.seek(0)
            h=hashlib.sha256()
            count=0
            for chunk in iter(lambda: readback.read(1024*1024), b""):
                count+=len(chunk)
                if count>row["size_bytes"]:
                    raise ValueError("REMOTE_SIZE_MISMATCH")
                h.update(chunk)
        if count!=row["size_bytes"] or h.hexdigest()!=row["sha256"]:
            raise ValueError("REMOTE_HASH_MISMATCH")
        # Provider path is internal to local receipt, never public CI log.
        accepted.append({"relative_path":row["relative_path"],
                         "size_bytes":count,"sha256":h.hexdigest(),
                         "remote_object":remote,"readback_verified":True})
    return accepted

def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--manifest",type=Path,required=True)
    p.add_argument("--remote-root",required=True)
    p.add_argument("--receipt",type=Path,required=True)
    p.add_argument("--apply",action="store_true")
    a=p.parse_args()
    result={"schema":"semper-drive-publish/v1","state":"BLOCKED"}
    code=2
    try:
        root=a.root.resolve(strict=True)
        remote_root=sanitized_remote_prefix(a.remote_root)
        raw=a.manifest.read_bytes()
        if len(raw)>1048576: raise ValueError("MANIFEST_TOO_LARGE")
        manifest=json.loads(raw)
        local=verify(root,manifest)
        rows=selection(manifest)
        result.update(
            run_id=manifest["run_id"],source_commit=manifest["source_commit"],
            manifest_sha256=local["manifest_sha256"],upload_count=len(rows),
            upload_bytes=sum(row["size_bytes"] for row in rows),
            file_digests=[{"relative_path":row["relative_path"],
                           "sha256":row["sha256"],"size_bytes":row["size_bytes"]}
                          for row in rows],
            # No location or token in plan receipts.
            state="PLAN_ONLY" if not a.apply else "REMOTE_UNKNOWN")
        if not a.apply:
            code=0
        elif not rows:
            result["state"]="NO_OFF_GIT_FILES"
            code=0
        else:
            # Only trust caller invocation on an ADC/OAuth-equipped local host,
            # never export rclone.conf or OAuth tokens into public GHA.
            upload=upload_and_verify(root,manifest,remote_root,rows)
            result["state"]="REMOTE_READBACK_VERIFIED"
            result["remote_objects"]=upload
            code=0
    except (OSError,ValueError,TypeError,KeyError):
        result["state"]="BLOCKED"
        result["failure_class"]="MANIFEST_OR_REMOTE_OR_READBACK_UNKNOWN"
        # Even when some remote files uploaded, no success receipt is emitted;
        # operator must reconcile via the known run_id before any retry.
    result["observed_utc"]=datetime.now(timezone.utc).isoformat()
    a.receipt.parent.mkdir(parents=True,exist_ok=True)
    a.receipt.write_text(json.dumps(result,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print("DRIVE_PUBLISH_STATE="+result["state"])
    return code

if __name__=="__main__":
    raise SystemExit(main())
