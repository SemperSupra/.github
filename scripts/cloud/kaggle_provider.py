#!/usr/bin/env python3
"""Bounded generic Kaggle provider operation.

This script is invoked only by a protected, reviewed default-branch GHA job.
It never runs on PRs. Kaggle native job version and source digest are retained.
Unknown quota, active jobs or provider semantics => deny, never retry blindly.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

try:
    from .cloud_contract import load
except ImportError:  # standalone GitHub Actions entrypoint
    from cloud_contract import load

# Exact known native states; substring checks such as "NOT_COMPLETE"
# can otherwise misclassify an unknown account job as terminal/idle.
ACTIVE_STATES = frozenset(("RUNNING", "QUEUED", "PENDING", "STARTING"))
TERMINAL_STATES = frozenset((
    "COMPLETE", "COMPLETED", "SUCCESS", "ERROR", "FAIL", "FAILED",
    "CANCEL", "CANCELED", "CANCELLED",
))

def kernel_status(raw, *, error="KERNEL_STATUS_UNKNOWN"):
    state = str(raw).strip().upper()
    if "." in state:
        prefix, state = state.rsplit(".", 1)
        if not prefix.endswith("STATUS"):
            raise ValueError(error)
    if state not in ACTIVE_STATES and state not in TERMINAL_STATES:
        raise ValueError(error)
    return state

def quota_or_deny(api, m):
    if m["accelerator"] == "cpu":
        return "CPU_QUOTA_NOT_APPLICABLE"
    q = api.quota_view()
    slot = q.tpu_quota if m["accelerator"].startswith("Tpu") else q.gpu_quota
    if not slot or slot.total_time_allowed is None or slot.time_used is None:
        raise ValueError("QUOTA_UNKNOWN")
    remaining = (slot.total_time_allowed - slot.time_used).total_seconds()
    if remaining < m["budget"]["min_remaining_seconds"]:
        raise ValueError("QUOTA_INSUFFICIENT")
    return int(remaining)

def no_duplicate_or_active(api, target):
    token = None
    seen = set()
    for _ in range(20):
        args = {"mine": True, "page_size": 100}
        if token:
            args["page_token"] = token
        page = api.kernels_list_with_response(**args)
        if not page or not hasattr(page, "kernels"):
            raise ValueError("KERNEL_LIST_UNKNOWN")
        for item in page.kernels or []:
            ref = getattr(item, "ref", None)
            if not isinstance(ref, str) or "/" not in ref:
                raise ValueError("KERNEL_REF_UNKNOWN")
            if ref.lower() == target.lower():
                raise ValueError("TARGET_ALREADY_EXISTS_NO_REPUSH")
            # Account-wide exclusion, not just this framework's namespace.
            # Kaggle quota and accelerator allocations are shared across
            # independent projects, including earlier latent/ad-rdte jobs.
            status = kernel_status(getattr(api.kernels_status(ref), "status", None),
                                   error="OTHER_KAGGLE_ACCOUNT_JOB_UNKNOWN")
            if status in ACTIVE_STATES:
                raise ValueError("OTHER_KAGGLE_ACCOUNT_JOB_ACTIVE")
            if status not in TERMINAL_STATES:
                raise ValueError("OTHER_KAGGLE_ACCOUNT_JOB_UNKNOWN")
        token = getattr(page, "next_page_token", None)
        if not token:
            return
        if token in seen:
            raise ValueError("PAGE_LOOP")
        seen.add(token)
    raise ValueError("PAGINATION_BOUND")

def account_census(api):
    """One credentialed GET-only account observation; never submits.

    Only aggregate counts and remaining GPU/TPU seconds leave the trusted
    environment. Unknown state and incomplete pagination fail closed.
    """
    api.authenticate()
    quota = api.quota_view()
    result = {}
    for resource, slot in (("gpu-t4x2", getattr(quota, "gpu_quota", None)),
                           ("tpu-v5e8", getattr(quota, "tpu_quota", None))):
        if slot is None or getattr(slot, "total_time_allowed", None) is None or \
                getattr(slot, "time_used", None) is None:
            raise ValueError("ACCOUNT_QUOTA_UNKNOWN")
        allowed = int(slot.total_time_allowed.total_seconds())
        spent = int(slot.time_used.total_seconds())
        if not 0 <= spent <= allowed:
            raise ValueError("ACCOUNT_QUOTA_INVALID")
        result[resource] = allowed - spent
    token = None
    seen = set()
    active = 0
    checked = 0
    for _ in range(20):
        opts = {"mine": True, "page_size": 100}
        if token:
            opts["page_token"] = token
        page = api.kernels_list_with_response(**opts)
        if page is None or not hasattr(page, "kernels"):
            raise ValueError("ACCOUNT_KERNEL_PAGE_UNKNOWN")
        for item in page.kernels or []:
            ref = getattr(item, "ref", None)
            if not isinstance(ref, str) or "/" not in ref:
                raise ValueError("ACCOUNT_KERNEL_REF_UNKNOWN")
            status = kernel_status(getattr(api.kernels_status(ref), "status", None),
                                   error="ACCOUNT_KERNEL_STATUS_UNKNOWN")
            checked += 1
            if status in ACTIVE_STATES:
                active += 1
            elif status not in TERMINAL_STATES:
                raise ValueError("ACCOUNT_KERNEL_STATUS_UNKNOWN")
        token = getattr(page, "next_page_token", None)
        if not token:
            return {
                "schema": "semper-kaggle-account-census/v1",
                "state": "OBSERVED",
                "observed_utc": datetime.now(timezone.utc).isoformat(),
                "quota_remaining_seconds": result,
                "native_active_count": active,
                "kernels_examined": checked,
                "scope": "one authenticated Kaggle account; aggregate only",
            }
        if not isinstance(token,str) or token in seen:
            raise ValueError("ACCOUNT_KERNEL_PAGE_LOOP")
        seen.add(token)
    raise ValueError("ACCOUNT_KERNEL_PAGINATION_BOUND")

def submit(api, m, workspace):
    api.authenticate()
    username = api.get_config_value(api.CONFIG_NAME_USER)
    if not isinstance(username, str) or not username or not username.replace("-", "").replace("_", "").isalnum():
        raise ValueError("ACCOUNT_IDENTITY_UNKNOWN")
    target = f"{username}/semper-{m['experiment_id']}"
    remaining = quota_or_deny(api, m)
    no_duplicate_or_active(api, target)
    with tempfile.TemporaryDirectory(prefix="semper-kaggle-") as tmp:
        path = Path(tmp)
        source = workspace / m["source"]
        shutil.copyfile(source, path / "job.py")
        copied = (path / "job.py").read_bytes()
        if hashlib.sha256(copied).hexdigest() != m["source_sha256"]:
            raise ValueError("SOURCE_HASH_CHANGED_BEFORE_PROVIDER_SUBMIT")
        config = {
            "id": target, "title": m["experiment_id"],
            "code_file": "job.py", "language": "python", "kernel_type": "script",
            "is_private": True, "enable_internet": False,
            "enable_gpu": m["accelerator"].startswith("Nvidia"),
            "enable_tpu": m["accelerator"].startswith("Tpu"),
            "machine_shape": m["accelerator"],
            "dataset_sources": [], "competition_sources": [],
            "kernel_sources": [], "model_sources": [],
        }
        (path / "kernel-metadata.json").write_text(json.dumps(config, sort_keys=True))
        kwargs = {"timeout": str(m["max_runtime_seconds"])}
        if m["accelerator"] != "cpu":
            kwargs["acc"] = m["accelerator"]
        result = api.kernels_push(str(path), **kwargs)
    if not result or getattr(result, "error", None):
        raise ValueError("SUBMISSION_FAILED_OR_UNKNOWN")
    native = getattr(result, "versionNumber", None)
    if not isinstance(native, int) or native < 1:
        raise ValueError("NATIVE_VERSION_UNKNOWN")
    return {"state": "SUBMITTED_NOT_COMPLETED", "native_ref": target,
            "native_version": native, "quota_seconds_before": remaining}

def require_native_version(api, user, slug, expected):
    # Inspect the provider's authoritative latest native version. This
    # prevents an old receipt from being silently attached to a new run.
    from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelRequest
    req = ApiGetKernelRequest()
    req.user_name, req.kernel_slug = user, slug
    with api.build_kaggle_client() as client:
        details = client.kernels.kernels_api_client.get_kernel(req)
    actual = getattr(getattr(details, "metadata", None), "current_version_number", None)
    if not isinstance(actual, int) or actual != expected:
        raise ValueError("NATIVE_VERSION_DRIFT_OR_UNKNOWN")

def observe(api, m, native_version):
    api.authenticate()
    username = api.get_config_value(api.CONFIG_NAME_USER)
    ref = f"{username}/semper-{m['experiment_id']}"
    if not isinstance(native_version, int) or native_version < 1:
        raise ValueError("NATIVE_VERSION_REQUIRED")
    # Protect against stale version and accidental observation of a new run.
    # Exact-version native acceptance must be corroborated by a project verifier.
    require_native_version(api, username, f"semper-{m['experiment_id']}", native_version)
    target = f"{ref}/{native_version}"
    status = kernel_status(getattr(api.kernels_status(target), "status", None),
                           error="PROVIDER_STATE_UNKNOWN")
    require_native_version(api, username, f"semper-{m['experiment_id']}", native_version)
    if status in ACTIVE_STATES:
        return {"state": "NONTERMINAL", "native_ref": ref, "native_version": native_version}
    if status in ("ERROR", "FAIL", "FAILED", "CANCEL", "CANCELED", "CANCELLED"):
        return {"state": "TERMINAL_FAILED", "native_ref": ref, "native_version": native_version}
    if status in ("COMPLETE", "COMPLETED", "SUCCESS"):
        return {"state": "PROVIDER_TERMINAL_SCIENCE_UNVERIFIED",
                "native_ref": ref, "native_version": native_version}
    raise ValueError("PROVIDER_STATE_UNKNOWN")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path)
    p.add_argument("--workspace", type=Path)
    p.add_argument("--action", choices=("census", "submit", "observe"), required=True)
    p.add_argument("--native-version", type=int, default=0)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    result = {"schema": "semper-cloud-provider/v1", "provider": "kaggle"}
    try:
        from kaggle import api
        if args.action == "census":
            if args.native_version:
                raise ValueError("NO_VERSION_ON_CENSUS")
            result.update(account_census(api))
        else:
            if args.manifest is None or args.workspace is None:
                raise ValueError("MANIFEST_REQUIRED")
            m = load(args.manifest, args.workspace, "kaggle")
            if args.action == "submit":
                if args.native_version:
                    raise ValueError("NO_VERSION_ON_SUBMIT")
                payload = submit(api, m, args.workspace)
            else:
                payload = observe(api, m, args.native_version)
            result.update(payload, experiment_id=m["experiment_id"],
                          source_sha256=m["source_sha256"])
        status = 0
    except Exception:
        # Do not print exception details (may contain provider URLs/headers).
        result.update(state="BLOCKED", failure_class="PROVIDER_OPERATION_FAILED_OR_UNKNOWN")
        status = 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print("KAGGLE_PROVIDER_STATE=" + result["state"])
    return status

if __name__ == "__main__":
    raise SystemExit(main())
