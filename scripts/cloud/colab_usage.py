#!/usr/bin/env python3
"""Read-only Colab entitlement/rate sensor; use ONLY on a trusted ADC-equipped host.

No Colab VM is allocated. No bearer tokens, account identity, raw CLI logs, or
credential files are written to GitHub artifacts. Unknown => BLOCKED.
"""
import argparse
from datetime import datetime, timezone
import json
import re
import subprocess
from pathlib import Path

NUMBER = r"([0-9]+(?:\.[0-9]+)?)"
PATTERNS = {
    "balance_ccu": rf"Current balance:\s*{NUMBER}\s*compute units",
    "rate_ccu_per_hour": rf"Usage rate:\s*{NUMBER}\s*/hr",
    "active_assignments": r"Active assignments:\s*([0-9]+)",
}

def parse_usage(stdout: str) -> dict:
    if len(stdout) > 10000:
        raise ValueError("COLAB_USAGE_OVERSIZED")
    values = {}
    for key, pattern in PATTERNS.items():
        matches = re.findall(pattern, stdout, flags=re.IGNORECASE)
        if len(matches) != 1:
            raise ValueError("COLAB_USAGE_SCHEMA_CHANGED")
        values[key] = int(matches[0]) if key == "active_assignments" else float(matches[0])
    if not 0 <= values["balance_ccu"] <= 1000000:
        raise ValueError("COLAB_BALANCE_INVALID")
    if not 0 <= values["rate_ccu_per_hour"] <= 1000000:
        raise ValueError("COLAB_RATE_INVALID")
    if not 0 <= values["active_assignments"] <= 1000000:
        raise ValueError("COLAB_ASSIGNMENTS_INVALID")
    return values

def evaluate(snapshot: dict, budget: dict) -> dict:
    balance, rate, assignments = (snapshot["balance_ccu"],
        snapshot["rate_ccu_per_hour"], snapshot["active_assignments"])
    max_ccu = budget["max_ccu"]
    reserve = budget["reserve_ccu"]
    # The existing rate measures current assignments. It does not predict the
    # cost of a not-yet-allocated GPU/TPU. Operator must enforce a separate
    # maximum CCU budget and terminate if the observed rate exceeds it.
    ready = (assignments == 0 and balance >= max_ccu + reserve)
    return {
        "admission": "BUDGET_PRECHECK_PASS_NOT_RESOURCE_RESERVED" if ready else "BLOCKED",
        "reason": ("NO_ACTIVE_ASSIGNMENTS_AND_RESERVE_OK" if ready
                   else "ACTIVE_ASSIGNMENT_OR_INSUFFICIENT_BALANCE"),
        "balance_ccu": balance, "observed_rate_ccu_per_hour": rate,
        "active_assignments": assignments, "reserve_ccu": reserve,
        "max_per_run_ccu": max_ccu,
        "prediction_boundary": "No prospective accelerator rate, exact plan renewal, entitlement eligibility, or availability inferred.",
    }

def collect(budget: dict) -> dict:
    try:
        p = subprocess.run(
            ["colab", "--auth=adc", "usage"], capture_output=True, text=True,
            timeout=45, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("COLAB_CLI_UNAVAILABLE")
    if p.returncode:
        raise ValueError("COLAB_USAGE_REQUEST_FAILED")
    return evaluate(parse_usage(p.stdout), budget)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--budget", type=Path, required=True,
                   help="Validated Colab experiment manifest JSON")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    try:
        manifest = json.loads(args.budget.read_text(encoding="utf-8"))
        if manifest["provider"] != "colab":
            raise ValueError("NOT_COLAB_MANIFEST")
        budget = manifest["budget"]
        if (set(budget) != {"max_ccu", "reserve_ccu", "assumed_max_ccu_per_hour"}
            or not all(type(v) in (int, float) for v in budget.values())):
            raise ValueError("COLAB_BUDGET_INVALID")
        snapshot = collect(budget)
        status = 0 if snapshot["admission"].startswith("BUDGET_PRECHECK_PASS") else 2
    except (OSError, ValueError, KeyError, TypeError):
        snapshot = {"admission": "BLOCKED", "reason": "ENTITLEMENT_OR_PARSER_UNKNOWN"}
        status = 2
    snapshot["schema"] = "semper-colab-usage/v1"
    snapshot["observed_utc"] = datetime.now(timezone.utc).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    print("COLAB_USAGE_ADMISSION=" + snapshot["admission"])
    return status

if __name__ == "__main__":
    raise SystemExit(main())
