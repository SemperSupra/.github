#!/usr/bin/env python3
"""Offline provider-neutral cloud experiment admission contract (stdlib only).

This is a *planning/validation* gate. It never authenticates, launches providers,
downloads private artifacts, or executes user-supplied code. Every GHA caller must
source-lock and approve a manifest on its protected default branch.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

SCHEMA = "semper-cloud-experiment/v1"
PROVIDERS = {
    "kaggle": {"cpu", "NvidiaTeslaT4", "TpuV5E8"},
    "colab": {"cpu", "T4", "L4", "A100", "v5e1", "v6e1"},
}
IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{2,49}$")
SHA = re.compile(r"^[0-9a-f]{64}$")
ERR = "CLOUD_CONTRACT_DENIED"

def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")

def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()

def valid_path(raw: object, *, root: str) -> Path:
    if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
        raise ValueError("INVALID_RELATIVE_PATH")
    p = PurePosixPath(raw)
    if (p.is_absolute() or len(p.parts) < 2 or p.parts[0] != root
            or any(s in ("", ".", "..") for s in p.parts)):
        raise ValueError("INVALID_RELATIVE_PATH")
    return Path(*p.parts)

def load(manifest_path: Path, workspace: Path, provider: str) -> dict:
    raw = manifest_path.read_bytes()
    if len(raw) > 16384:
        raise ValueError("MANIFEST_TOO_LARGE")
    try:
        m = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise ValueError("MANIFEST_JSON_INVALID")
    required = {"schema", "experiment_id", "provider", "accelerator",
                "source", "source_sha256", "max_runtime_seconds",
                "receipt_file", "budget"}
    if not isinstance(m, dict) or set(m) != required:
        raise ValueError("MANIFEST_FIELDS_INVALID")
    if m["schema"] != SCHEMA or m["provider"] != provider or provider not in PROVIDERS:
        raise ValueError("PROVIDER_SCHEMA_MISMATCH")
    if not isinstance(m["experiment_id"], str) or not IDENTIFIER.fullmatch(m["experiment_id"]):
        raise ValueError("EXPERIMENT_ID_INVALID")
    if m["accelerator"] not in PROVIDERS[provider]:
        raise ValueError("ACCELERATOR_NOT_ALLOWLISTED")
    if type(m["max_runtime_seconds"]) is not int or not 60 <= m["max_runtime_seconds"] <= 3600:
        raise ValueError("RUNTIME_BOUND_INVALID")
    src = valid_path(m["source"], root="experiments")
    if src.suffix != ".py":
        raise ValueError("PYTHON_SOURCE_REQUIRED")
    receipt = valid_path(m["receipt_file"], root="output")
    if receipt.suffix != ".json":
        raise ValueError("RECEIPT_PATH_INVALID")
    if not isinstance(m["source_sha256"], str) or not SHA.fullmatch(m["source_sha256"]):
        raise ValueError("SOURCE_DIGEST_INVALID")
    workspace = workspace.resolve(strict=True)
    full = workspace / src
    if full.is_symlink() or not full.is_file() or not full.resolve().is_relative_to(workspace):
        raise ValueError("SOURCE_NOT_SAFE")
    source_bytes = full.read_bytes()
    if not 1 <= len(source_bytes) <= 262144:
        raise ValueError("SOURCE_SIZE_INVALID")
    if hashlib.sha256(source_bytes).hexdigest() != m["source_sha256"]:
        raise ValueError("SOURCE_HASH_MISMATCH")
    b = m["budget"]
    if provider == "kaggle":
        if (not isinstance(b, dict) or set(b) != {"min_remaining_seconds"}
                or type(b["min_remaining_seconds"]) is not int
                or not 300 <= b["min_remaining_seconds"] <= 604800
                or b["min_remaining_seconds"] < m["max_runtime_seconds"] + 300):
            raise ValueError("KAGGLE_QUOTA_GATE_INVALID")
    else:
        if (not isinstance(b, dict)
                or set(b) != {"max_ccu", "reserve_ccu", "assumed_max_ccu_per_hour"}
                or any(type(b[k]) not in (int, float) or not 0 <= b[k] <= 1000 for k in b)
                or b["max_ccu"] <= 0 or b["assumed_max_ccu_per_hour"] <= 0
                or b["max_ccu"] > 50
                or b["assumed_max_ccu_per_hour"] * m["max_runtime_seconds"] / 3600
                   > b["max_ccu"]):
            raise ValueError("COLAB_BUDGET_GATE_INVALID")
    return m

def plan(m: dict, workspace: Path, manifest: Path) -> dict:
    return {
        "schema": "semper-cloud-plan/v1",
        "experiment_id": m["experiment_id"],
        "provider": m["provider"],
        "accelerator": m["accelerator"],
        "runtime_limit_seconds": m["max_runtime_seconds"],
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "source_sha256": m["source_sha256"],
        "workspace_label": workspace.name,  # non-secret display only
        "state": "PLANNED_NOT_SUBMITTED",
        "budget": m["budget"],
    }

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--provider", choices=tuple(PROVIDERS), required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    try:
        m = load(a.manifest, a.workspace, a.provider)
        result = plan(m, a.workspace, a.manifest)
        code = 0
    except (OSError, ValueError) as e:
        result = {"schema": "semper-cloud-plan/v1", "provider": a.provider,
                  "state": "BLOCKED", "failure_class": str(e)[:64]}
        code = 2
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print("CLOUD_PLAN_STATE=" + result["state"])
    return code

if __name__ == "__main__":
    raise SystemExit(main())
