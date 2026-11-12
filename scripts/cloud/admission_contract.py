#!/usr/bin/env python3
"""Provider-neutral account admission *proposal* from durable GitHub authority.

Zero credentials, network, Git mutations, scheduling or provider calls.
A separate trusted controller must compare-and-swap the durable ledger,
observe all native account work, and acquire a FENCED lease before actuation.
This module never grants itself a lease or submits a job.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

SCHEMA = "semper-cloud-admission/v1"
REQUEST = "semper-cloud-request/v1"
LEDGER = "semper-cloud-ledger/v1"
CENSUS = "semper-cloud-account-census/v1"
REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
SHA = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_RESOURCES = {
    "kaggle": {"cpu", "gpu-t4x2", "tpu-v5e8"},
    "colab": {"cpu", "gpu-t4", "gpu-l4", "tpu-v5e1"},
}
MAX_REQUESTS = 100
MAX_AGE_SECONDS = 900

def encode(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")

def digest(value: object) -> str:
    return hashlib.sha256(encode(value)).hexdigest()

def fail(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)

def utc(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise ValueError("TIME_INVALID")
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("TIMEZONE_REQUIRED")
    return result.astimezone(timezone.utc)

def validate_request(r: dict) -> dict:
    fields = {"schema", "request_id", "project_repository", "source_commit",
              "manifest_path", "manifest_sha256", "provider", "resource",
              "account_alias", "max_runtime_seconds", "max_ccu",
              "priority", "submitted_utc"}
    fail(isinstance(r, dict) and set(r) == fields and r["schema"] == REQUEST,
         "REQUEST_SCHEMA_INVALID")
    fail(isinstance(r["request_id"], str) and IDENTIFIER.fullmatch(r["request_id"]) is not None,
         "REQUEST_ID_INVALID")
    fail(isinstance(r["project_repository"], str) and REPO.fullmatch(r["project_repository"]) is not None,
         "REPOSITORY_INVALID")
    fail(isinstance(r["source_commit"], str) and COMMIT.fullmatch(r["source_commit"]) is not None
         and isinstance(r["manifest_sha256"], str) and SHA.fullmatch(r["manifest_sha256"]) is not None,
         "SOURCE_IDENTITY_INVALID")
    path=r["manifest_path"]
    fail(isinstance(path,str) and len(path) <= 200 and "\\" not in path and
         not path.startswith("/") and
         len(PurePosixPath(path).parts) >= 2 and
         PurePosixPath(path).parts[0] in ("cloud","experiments") and
         all(p not in (".","..") for p in path.split("/")) and path.endswith(".json"),
         "MANIFEST_PATH_INVALID")
    provider=r["provider"]
    fail(provider in ALLOWED_RESOURCES and r["resource"] in ALLOWED_RESOURCES[provider],
         "RESOURCE_NOT_ADMITTED")
    fail(isinstance(r["account_alias"],str) and IDENTIFIER.fullmatch(r["account_alias"]) is not None,
         "ACCOUNT_ALIAS_INVALID")
    fail(type(r["priority"]) is int and 0 <= r["priority"] <= 3,
         "PRIORITY_INVALID")
    fail(type(r["max_runtime_seconds"]) is int and 60 <= r["max_runtime_seconds"] <= 3600,
         "RUNTIME_INVALID")
    fail(type(r["max_ccu"]) in (int,float) and 0 <= r["max_ccu"] <= 5,
         "BUDGET_INVALID")
    if provider == "kaggle":
        fail(r["max_ccu"] == 0, "KAGGLE_CCU_MUST_BE_ZERO")
    else:
        fail(r["max_ccu"] > 0, "COLAB_CCU_BUDGET_REQUIRED")
    utc(r["submitted_utc"])
    return r

def validate_ledger(ledger: dict) -> dict:
    fail(isinstance(ledger, dict) and set(ledger) == {
         "schema", "provider", "account_alias", "generation", "lease"} and
         ledger["schema"] == LEDGER, "LEDGER_SCHEMA_INVALID")
    fail(ledger["provider"] in ALLOWED_RESOURCES and isinstance(ledger["account_alias"], str)
         and IDENTIFIER.fullmatch(ledger["account_alias"]) is not None,
         "LEDGER_SCOPE_INVALID")
    fail(type(ledger["generation"]) is int and ledger["generation"] >= 0,
         "GENERATION_INVALID")
    lease=ledger["lease"]
    if lease is not None:
        fail(isinstance(lease,dict) and set(lease) == {
            "request_id","request_sha256","fence","native_state","native_ref",
            "native_version","expires_utc"}, "LEASE_SCHEMA_INVALID")
        fail(type(lease["fence"]) is int and lease["fence"] == ledger["generation"],
             "LEASE_FENCE_INVALID")
        fail(isinstance(lease["request_sha256"],str)
             and SHA.fullmatch(lease["request_sha256"]) is not None
             and isinstance(lease["request_id"],str)
             and IDENTIFIER.fullmatch(lease["request_id"]) is not None,
             "LEASE_IDENTITY_INVALID")
        fail(lease["native_state"] in ("RESERVED","SUBMITTED","QUEUED","RUNNING",
                                      "UNKNOWN","TERMINAL_UNVERIFIED"),
             "LEASE_STATE_INVALID")
        if lease["native_ref"] is not None:
            fail(isinstance(lease["native_ref"],str) and 1<=len(lease["native_ref"])<=160,
                 "LEASE_NATIVE_REF_INVALID")
        fail(lease["native_version"] is None or
             type(lease["native_version"]) is int and lease["native_version"] >= 1,
             "LEASE_NATIVE_VERSION_INVALID")
        utc(lease["expires_utc"])
    return ledger

def validate_census(census: dict, ledger: dict, now: datetime) -> None:
    fields={"schema","provider","account_alias","observed_utc","status",
            "native_active_count","quota_remaining_seconds","balance_ccu",
            "reserved_ccu","active_assignments"}
    fail(isinstance(census,dict) and set(census)==fields
         and census["schema"] == CENSUS and census["status"]=="OBSERVED",
         "CENSUS_SCHEMA_UNKNOWN")
    fail(census["provider"] == ledger["provider"] and
         census["account_alias"] == ledger["account_alias"],
         "ACCOUNT_CENSUS_MISMATCH")
    age=(now-utc(census["observed_utc"])).total_seconds()
    fail(0 <= age <= MAX_AGE_SECONDS, "CENSUS_STALE_OR_FUTURE")
    fail(type(census["native_active_count"]) is int and
         census["native_active_count"] >= 0, "NATIVE_STATUS_UNKNOWN")
    fail(type(census["active_assignments"]) is int and
         census["active_assignments"] >= 0, "ASSIGNMENTS_UNKNOWN")
    if ledger["provider"]=="kaggle":
        quotas=census["quota_remaining_seconds"]
        fail(isinstance(quotas,dict) and
             set(quotas)=={"gpu-t4x2","tpu-v5e8"} and
             all(type(x) is int and x >= 0 for x in quotas.values()),
             "KAGGLE_QUOTA_UNKNOWN")
    else:
        for key in ("balance_ccu","reserved_ccu"):
            fail(type(census[key]) in (int,float) and 0 <= census[key] <= 100000,
                 "COLAB_METER_UNKNOWN")

def propose(requests: list[dict], approvals: list[str], ledger: dict,
            census: dict, now_utc: str) -> dict:
    """Compute a non-authoritative claim candidate. No lease is granted."""
    now=utc(now_utc)
    validate_ledger(ledger)
    if not isinstance(requests,list) or len(requests) > MAX_REQUESTS:
        raise ValueError("REQUEST_LIST_BOUND")
    if not isinstance(approvals,list) or len(approvals) > MAX_REQUESTS or any(
            not isinstance(x,dict) or set(x) != {"request_id","request_sha256"}
            or not isinstance(x["request_id"],str)
            or IDENTIFIER.fullmatch(x["request_id"]) is None
            or not isinstance(x["request_sha256"],str)
            or SHA.fullmatch(x["request_sha256"]) is None for x in approvals):
        raise ValueError("APPROVAL_INPUT_INVALID")
    approved={x["request_id"]:x["request_sha256"] for x in approvals}
    if len(approved)!=len(approvals):
        raise ValueError("DUPLICATE_APPROVAL")
    clean=[validate_request(r) for r in requests]
    ids=[r["request_id"] for r in clean]
    if len(ids)!=len(set(ids)):
        raise ValueError("DUPLICATE_REQUEST_ID")
    # A long-expired lease is still a lease until native terminal evidence and
    # an independently reconciled durable compare-and-swap clear it.
    if ledger["lease"] is not None:
        lease=ledger["lease"]
        state="RECONCILE_BEFORE_RECLAIM" if utc(lease["expires_utc"])<=now \
              else "OBSERVE_EXISTING_LEASE"
        return {"schema":SCHEMA,"state":state,"account_alias":ledger["account_alias"],
                "provider":ledger["provider"],"fence":ledger["generation"],
                "lease_request_id":lease["request_id"],
                "next_action":"OBSERVE_NATIVE_AND_RECONCILE_NOT_SUBMIT"}
    validate_census(census,ledger,now)
    if census["native_active_count"] or census["active_assignments"]:
        return {"schema":SCHEMA,"state":"HOLD_NATIVE_ACCOUNT_BUSY",
                "provider":ledger["provider"],"account_alias":ledger["account_alias"],
                "next_action":"RECONCILE_ORPHAN_NATIVE_NOT_SUBMIT"}
    permitted=[r for r in clean if r["provider"]==ledger["provider"]
               and r["account_alias"]==ledger["account_alias"]
               and approved.get(r["request_id"]) == digest(r)
               and utc(r["submitted_utc"]) <= now]
    # Request approval is independent of request-supplied fields and must be
    # provided by the owning DLE / trusted authorization boundary.
    permitted.sort(key=lambda r:(-r["priority"],utc(r["submitted_utc"]),
                                 r["request_id"]))
    for r in permitted:
        if ledger["provider"]=="kaggle":
            # GPU and TPU are separately metered; never let healthy GPU
            # quota authorize a scarce/unknown TPU request, or vice versa.
            if (r["resource"] != "cpu" and
                census["quota_remaining_seconds"][r["resource"]] <
                r["max_runtime_seconds"]+300):
                continue
        else:
            if census["balance_ccu"] < r["max_ccu"]+census["reserved_ccu"]:
                continue
        ledger_hash=digest(ledger)
        req_hash=digest(r)
        generation=ledger["generation"]+1
        return {
            "schema":SCHEMA,"state":"ADMISSIBLE_NOT_CLAIMED",
            "provider":ledger["provider"],"account_alias":ledger["account_alias"],
            "request_id":r["request_id"],"request_sha256":req_hash,
            "project_repository":r["project_repository"],
            "source_commit":r["source_commit"],
            "manifest_sha256":r["manifest_sha256"],
            "expected_ledger_sha256":ledger_hash,
            "proposed_fence":generation,
            "claim_token_sha256":digest({
                "request_sha256":req_hash,"expected_ledger_sha256":ledger_hash,
                "fence":generation,"account_alias":ledger["account_alias"]}),
            "next_action":"TRUSTED_CAS_LEASE_THEN_REOBSERVE_NATIVE_BEFORE_SUBMIT",
            "claim_boundary":"A proposal is NOT a lock, reservation, allocation or dispatch."
        }
    return {"schema":SCHEMA,"state":"NO_ELIGIBLE_APPROVED_REQUEST",
            "provider":ledger["provider"],"account_alias":ledger["account_alias"],
            "next_action":"NO_SUBMIT"}

def prepare_fenced_claim(ledger: dict, decision: dict, now_utc: str) -> dict:
    """Render a CAS-ready ledger *candidate*, never save it.

    The caller MUST condition its durable GitHub write on the exact ledger
    object identity/revision, and reobserve the provider after winning CAS.
    Two concurrent callers may render the same object; only one may write.
    """
    validate_ledger(ledger)
    now=utc(now_utc)
    fail(isinstance(decision,dict)
         and decision.get("schema")==SCHEMA
         and decision.get("state")=="ADMISSIBLE_NOT_CLAIMED",
         "PROPOSAL_NOT_ADMISSIBLE")
    fail(ledger["lease"] is None, "LEASE_ALREADY_PRESENT")
    fail(decision.get("provider")==ledger["provider"]
         and decision.get("account_alias")==ledger["account_alias"],
         "PROPOSAL_SCOPE_MISMATCH")
    fail(decision.get("expected_ledger_sha256")==digest(ledger),
         "PROPOSAL_LEDGER_STALE")
    fail(type(decision.get("proposed_fence")) is int
         and decision["proposed_fence"]==ledger["generation"]+1,
         "PROPOSAL_FENCE_INVALID")
    req_id=decision.get("request_id")
    req_sha=decision.get("request_sha256")
    fail(isinstance(req_id,str) and IDENTIFIER.fullmatch(req_id) is not None
         and isinstance(req_sha,str) and SHA.fullmatch(req_sha) is not None,
         "PROPOSAL_REQUEST_INVALID")
    token=digest({"request_sha256":req_sha,
                  "expected_ledger_sha256":digest(ledger),
                  "fence":decision["proposed_fence"],
                  "account_alias":ledger["account_alias"]})
    fail(decision.get("claim_token_sha256")==token,"PROPOSAL_TOKEN_INVALID")
    new=dict(ledger)
    new["generation"]=decision["proposed_fence"]
    new["lease"]={
        "request_id":req_id,
        "request_sha256":req_sha,
        "fence":new["generation"],
        "native_state":"RESERVED",
        "native_ref":None,
        "native_version":None,
        "expires_utc":(now+timedelta(minutes=5)).isoformat().replace("+00:00","Z")
    }
    validate_ledger(new)
    return new

MAX_INPUT_BYTES = 1024 * 1024  # Untrusted Git-backed JSON, never a raw provider payload.

def read_strict_json(path: Path) -> object:
    """Reject ambiguous keys/nonfinite values before making an admission plan.

    The independent owner approval must still bind the exact normalized
    request digest; this parser does not sign, authorize, claim or submit.
    """
    with path.open("rb") as source:
        raw = source.read(MAX_INPUT_BYTES + 1)
    fail(len(raw) <= MAX_INPUT_BYTES, "INPUT_TOO_LARGE")

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            fail(key not in result, "JSON_DUPLICATE_KEY")
            result[key] = value
        return result

    def nonfinite(_value):
        raise ValueError("JSON_NONFINITE_NUMBER")

    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys,
                      parse_constant=nonfinite)

def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument("--requests",type=Path,required=True)
    p.add_argument("--approvals",type=Path,required=True)
    p.add_argument("--ledger",type=Path,required=True)
    p.add_argument("--census",type=Path,required=True)
    p.add_argument("--now-utc",required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    try:
        read=read_strict_json
        result=propose(read(a.requests),read(a.approvals),read(a.ledger),
                       read(a.census),a.now_utc)
        status=0
    except (OSError,ValueError,TypeError,KeyError,OverflowError):
        result={"schema":SCHEMA,"state":"BLOCKED_INVALID_OR_UNKNOWN",
                "next_action":"NO_SUBMIT"}
        status=2
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
    print("CLOUD_ADMISSION_STATE="+result["state"])
    return status

if __name__=="__main__":
    raise SystemExit(main())
