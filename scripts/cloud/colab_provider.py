#!/usr/bin/env python3
"""Trusted-local Colab CPU/GPU/TPU one-shot runner; never use on public GHA.

Expects preexisting human-approved ADC in the host's own user profile. Uses a
known session ID, bounded remote execution, downloaded receipt, and explicit
stop in finally. No Google credentials ever flow through a public workflow.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from cloud_contract import load
from colab_usage import collect

def cli(argv, timeout):
    try:
        p = subprocess.run(["colab", "--auth=adc", *argv],
                           capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("COLAB_CLI_EXECUTION_UNKNOWN")
    if p.returncode:
        raise ValueError("COLAB_PROVIDER_COMMAND_FAILED")
    return p

def execute(m, workspace, output):
    pre = collect(m["budget"])
    if pre["admission"] != "BUDGET_PRECHECK_PASS_NOT_RESOURCE_RESERVED":
        raise ValueError("COLAB_BUDGET_OR_SESSION_BLOCKED")
    session = "semper-" + m["experiment_id"]
    accelerator = m["accelerator"]
    flags = ([] if accelerator == "cpu" else
             ["--tpu", accelerator] if accelerator.startswith("v") else
             ["--gpu", accelerator])
    started = False
    verified_cleanup = False
    downloaded = None
    failure = None
    before = pre["balance_ccu"]
    try:
        cli(["new", "-s", session, *flags], 150)
        started = True
        cli(["exec", "-s", session, "-f", str(workspace / m["source"])],
            m["max_runtime_seconds"] + 60)
        # Project owns remote receipt contract; never trust it without the
        # independent project-specific evaluator.
        expected = m["receipt_file"]
        path = output / "scientific-unverified.json"
        cli(["download", "-s", session, expected, str(path)], 90)
        if not path.is_file() or path.stat().st_size > 65536:
            raise ValueError("COLAB_SCIENTIFIC_RECEIPT_NOT_FOUND")
        downloaded = hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        failure = "COLAB_EXEC_OR_RECOVERY_UNKNOWN"
    finally:
        if started:
            try:
                cli(["stop", "-s", session], 90)
                verified_cleanup = True
            except Exception:
                verified_cleanup = False
    try:
        post = collect(m["budget"])
        after = post["balance_ccu"]
        delta = round(before - after, 6)
    except Exception:
        after, delta = None, None
    if failure or not verified_cleanup or delta is None:
        raise ValueError("EXECUTION_OR_CLEANUP_OR_METER_UNKNOWN")
    if delta < -0.00001:
        raise ValueError("CCU_ACCOUNTING_UNKNOWN")
    return {
        "state": "PROVIDER_COMPLETED_SCIENCE_UNVERIFIED",
        "experiment_id": m["experiment_id"], "source_sha256": m["source_sha256"],
        "scientific_unverified_sha256": downloaded,
        "balance_before_ccu": before, "balance_after_ccu": after,
        "observed_ccu_delta": delta,
        "ccu_budget_exceeded": delta > m["budget"]["max_ccu"],
        "known_session_stopped": verified_cleanup,
        "meter_boundary": "Charges may settle after observation. Query account again later.",
    }

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--action", choices=("usage", "run"), required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    report = {"schema": "semper-cloud-provider/v1", "provider": "colab",
              "observed_utc": datetime.now(timezone.utc).isoformat()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        m = load(args.manifest, args.workspace, "colab")
        if args.action == "usage":
            r = collect(m["budget"])
            report.update(r, state=r["admission"])
            code = 0 if r["admission"].startswith("BUDGET_PRECHECK_PASS") else 2
        else:
            report.update(execute(m, args.workspace, args.output.parent))
            code = 2 if report.get("ccu_budget_exceeded") else 0
    except Exception:
        report.update(state="BLOCKED", failure_class="COLAB_OPERATION_OR_CLEANUP_UNKNOWN")
        code = 2
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print("COLAB_PROVIDER_STATE=" + report["state"])
    return code

if __name__ == "__main__":
    raise SystemExit(main())
