#!/usr/bin/env python3
"""Build and verify immutable off-Git experiment artifact manifests.

No cloud credentials, network, or Git writes. Operates on an explicitly scoped
bundle directory and produces a canonical SHA256 manifest for durable DLE.
Separate trusted-host publishing transports only the listed verified payloads.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import sys

SCHEMA = "semper-research-artifacts/v1"
RUN_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,95}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SHA = re.compile(r"^[0-9a-f]{64}$")
CLASSIFICATIONS = ("public-synthetic", "private-research")
MAX_FILES = 400
MAX_MANIFEST_BYTES = 1048576
MAX_FILE_BYTES = 10 * 1024**3
DEFAULT_GIT_THRESHOLD = 512 * 1024
DEFAULT_LFS_THRESHOLD = 50 * 1024**2
IGNORED = {"artifact-manifest.json", "upload-receipt.json"}

def canonical(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()

def digest(b):
    return hashlib.sha256(b).hexdigest()

def safe_files(root: Path):
    if root.is_symlink() or not root.is_dir():
        raise ValueError("BUNDLE_ROOT_INVALID")
    if not root.resolve().is_dir():
        raise ValueError("BUNDLE_ROOT_INVALID")
    files=[]
    for p in sorted(root.rglob("*")):
        rel=p.relative_to(root).as_posix()
        if p.is_symlink() or not p.resolve().is_relative_to(root.resolve()):
            raise ValueError("LINK_OR_PATH_ESCAPE_DENIED")
        mode=p.stat().st_mode
        if p.is_dir():
            continue
        if not stat.S_ISREG(mode) or not rel or "\\" in rel or "\x00" in rel:
            raise ValueError("INVALID_PAYLOAD_TYPE")
        if p.name in IGNORED or p.name.startswith("."):
            raise ValueError("CONTROL_FILE_IN_PAYLOAD_DENIED")
        if p.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("PAYLOAD_TOO_LARGE")
        files.append((rel,p))
        if len(files)>MAX_FILES:
            raise ValueError("TOO_MANY_FILES")
    if not files:
        raise ValueError("EMPTY_ARTIFACT_BUNDLE")
    return files

def storage_route(size, classification, extension):
    # Recommend only, never write to Git/LFS by this helper.
    # Private material never automatically enters public Git.
    if classification=="private-research":
        return "drive"
    if size <= DEFAULT_GIT_THRESHOLD and extension in (".json", ".jsonl", ".csv", ".txt", ".md", ".svg", ".yaml", ".yml"):
        return "git-candidate"
    if size <= DEFAULT_LFS_THRESHOLD and extension in (".png", ".pdf", ".pt", ".safetensors", ".parquet"):
        return "git-lfs-candidate"
    return "drive"

def build(root: Path, run_id: str, repo: str, source_sha: str, classification: str):
    if not RUN_ID.fullmatch(run_id) or "/" not in repo or not COMMIT.fullmatch(source_sha):
        raise ValueError("PROVENANCE_IDENTIFIERS_INVALID")
    if classification not in CLASSIFICATIONS:
        raise ValueError("CLASSIFICATION_INVALID")
    rows=[]
    for rel,p in safe_files(root):
        size=p.stat().st_size
        rows.append({"relative_path": rel, "size_bytes": size,
                     "sha256": digest(p.read_bytes()),
                     "recommended_storage": storage_route(size,classification,p.suffix.lower())})
    return {
        "schema": SCHEMA,
        "run_id": run_id,
        "source_repository": repo,
        "source_commit": source_sha,
        "classification": classification,
        "files": rows,
        "evidence_boundary": "Storage manifest, not scientific validation or authorization to publish.",
    }

def verify(root: Path, manifest: dict):
    if not isinstance(manifest,dict) or manifest.get("schema")!=SCHEMA:
        raise ValueError("MANIFEST_SCHEMA_INVALID")
    if manifest.get("classification") not in CLASSIFICATIONS:
        raise ValueError("MANIFEST_CLASSIFICATION_INVALID")
    if (not RUN_ID.fullmatch(str(manifest.get("run_id", ""))) or
        not COMMIT.fullmatch(str(manifest.get("source_commit", "")))):
        raise ValueError("MANIFEST_PROVENANCE_INVALID")
    rows=manifest.get("files")
    if not isinstance(rows,list) or len(rows)<1 or len(rows)>MAX_FILES:
        raise ValueError("MANIFEST_ROWS_INVALID")
    actual=safe_files(root)
    if len(actual)!=len(rows):
        raise ValueError("FILE_SET_MISMATCH")
    expected={x["relative_path"]: x for x in rows}
    if len(expected)!=len(rows) or set(expected)!=set(rel for rel,_ in actual):
        raise ValueError("FILE_SET_MISMATCH")
    for rel,p in actual:
        x=expected[rel]
        size=p.stat().st_size
        if not isinstance(x.get("size_bytes"),int) or x["size_bytes"]!=size:
            raise ValueError("FILE_SIZE_MISMATCH")
        if not SHA.fullmatch(str(x.get("sha256",""))) or x["sha256"]!=digest(p.read_bytes()):
            raise ValueError("FILE_HASH_MISMATCH")
        if x.get("recommended_storage")!=storage_route(size, manifest["classification"],p.suffix.lower()):
            raise ValueError("STORAGE_ROUTE_TAMPERED")
    return {"schema":"semper-artifact-verification/v1","run_id":manifest["run_id"],
            "manifest_sha256":digest(canonical(manifest)),
            "file_count":len(rows),"total_bytes":sum(x["size_bytes"] for x in rows),
            "status":"VERIFIED_LOCAL_NOT_UPLOADED"}

def main():
    p=argparse.ArgumentParser()
    sub=p.add_subparsers(dest="action",required=True)
    a=sub.add_parser("build")
    for flag in ("root","run-id","repo","source-commit","classification","output"):
        a.add_argument("--"+flag, required=True)
    v=sub.add_parser("verify")
    v.add_argument("--root",required=True)
    v.add_argument("--manifest",required=True)
    v.add_argument("--output",required=True)
    o=p.parse_args()
    try:
        if o.action=="build":
            manifest=build(Path(o.root),o.run_id,o.repo,o.source_commit,o.classification)
            result=manifest
        else:
            raw=Path(o.manifest).read_bytes()
            if len(raw)>MAX_MANIFEST_BYTES:
                raise ValueError("MANIFEST_TOO_LARGE")
            result=verify(Path(o.root),json.loads(raw.decode()))
        Path(o.output).parent.mkdir(parents=True,exist_ok=True)
        Path(o.output).write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
        print("ARTIFACT_CONTRACT="+("BUILT" if o.action=="build" else "VERIFIED"))
        return 0
    except (OSError,ValueError,TypeError,KeyError):
        print("ARTIFACT_CONTRACT=BLOCKED",file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
