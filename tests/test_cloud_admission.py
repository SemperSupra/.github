"""No-provider, no-Git, no-secret admission planner qualification.

All fixtures are public-synthetic, with approvals from an independent mock
authority. Tests never create leases, allocate resources or mutate a DLE.
"""
from datetime import datetime, timedelta, timezone
import copy
import tempfile
from pathlib import Path
import unittest

from scripts.cloud.admission_contract import (
    REQUEST, LEDGER, CENSUS, SCHEMA, digest, propose, validate_request,
    prepare_fenced_claim, read_strict_json,
)

NOW = "2026-11-12T12:00:00Z"
SOURCE = "0123456789abcdef" * 2 + "01234567"
HASH = "e"*64

def req(name="study-001", provider="kaggle", priority=1, minute=0):
    return {
        "schema":REQUEST,
        "request_id":name,"project_repository":"SemperSupra/latent-reasoning",
        "source_commit":SOURCE,
        "manifest_path":"cloud/study.json","manifest_sha256":HASH,
        "provider":provider,"resource":"gpu-t4x2" if provider=="kaggle" else "gpu-t4",
        "account_alias":"shared-account-001",
        "max_runtime_seconds":600,
        "max_ccu":0 if provider=="kaggle" else 3,
        "priority":priority,
        "submitted_utc":(datetime.fromisoformat("2026-11-12T10:00:00+00:00")
                         +timedelta(minutes=minute)).isoformat(),
    }

def ledger(provider="kaggle"):
    return {"schema":LEDGER,"provider":provider,
            "account_alias":"shared-account-001",
            "generation":3,"lease":None}

def census(provider="kaggle"):
    return {
        "schema":CENSUS,"provider":provider,
        "account_alias":"shared-account-001",
        "observed_utc":"2026-11-12T11:59:00Z",
        "status":"OBSERVED",
        "native_active_count":0, "active_assignments":0,
        "quota_remaining_seconds":{"gpu-t4x2":3600,"tpu-v5e8":3600}
        if provider=="kaggle" else None,
        "balance_ccu":75 if provider=="colab" else None,
        "reserved_ccu":20 if provider=="colab" else None,
    }

def approvals(*requests):
    return [{"request_id":r["request_id"],"request_sha256":digest(r)}
            for r in requests]

class AdmissionContractTests(unittest.TestCase):
    def test_kaggle_single_bounded_claim(self):
        r=req()
        out=propose([r],approvals(r),ledger(),census(),NOW)
        self.assertEqual(out["schema"],SCHEMA)
        self.assertEqual(out["state"],"ADMISSIBLE_NOT_CLAIMED")
        self.assertEqual(out["proposed_fence"],4)
        self.assertEqual(out["request_sha256"],digest(r))
        self.assertEqual(out["expected_ledger_sha256"],digest(ledger()))
        self.assertEqual(len(out["claim_token_sha256"]),64)
        self.assertIn("CAS",out["next_action"])

    def test_idempotent_proposal_is_not_a_lease(self):
        r=req(); l=ledger(); c=census()
        a=propose([r],approvals(r),l,c,NOW)
        b=propose([r],approvals(r),l,c,NOW)
        self.assertEqual(a,b)
        self.assertIsNone(l["lease"])
        self.assertEqual(l["generation"],3)

    def test_colab_budget_and_quiescence(self):
        r=req(provider="colab")
        out=propose([r],approvals(r),ledger("colab"),census("colab"),NOW)
        self.assertEqual(out["state"],"ADMISSIBLE_NOT_CLAIMED")
        self.assertEqual(out["provider"],"colab")

    def test_unapproved_request_cannot_be_selected(self):
        r=req()
        out=propose([r],[],ledger(),census(),NOW)
        self.assertEqual(out["state"],"NO_ELIGIBLE_APPROVED_REQUEST")

    def test_mutated_approved_request_is_not_authorized(self):
        r=req()
        a=approvals(r)
        r["max_runtime_seconds"]=1800
        out=propose([r],a,ledger(),census(),NOW)
        self.assertEqual(out["state"],"NO_ELIGIBLE_APPROVED_REQUEST")

    def test_queue_priority_and_fifo(self):
        lower=req("study-low",priority=1)
        high_old=req("study-hig-old",priority=2,minute=3)
        high_new=req("study-hig-new",priority=2,minute=7)
        rs=[lower,high_new,high_old]
        out=propose(rs,approvals(*rs),ledger(),census(),NOW)
        self.assertEqual(out["request_id"],high_old["request_id"])

    def test_existing_lease_blocks_even_if_provider_census_idle(self):
        r=req(); l=ledger()
        l["lease"]={
          "request_id":"study-previous","request_sha256":HASH,
          "fence":3,"native_state":"RUNNING",
          "native_ref":"user/study-previous","native_version":1,
          "expires_utc":"2026-11-12T12:30:00Z"}
        out=propose([r],approvals(r),l,census(),NOW)
        self.assertEqual(out["state"],"OBSERVE_EXISTING_LEASE")
        self.assertIn("NOT_SUBMIT",out["next_action"])

    def test_expired_lease_is_not_automatically_stolen(self):
        r=req(); l=ledger()
        l["lease"]={
          "request_id":"study-previous","request_sha256":HASH,
          "fence":3,"native_state":"UNKNOWN",
          "native_ref":None,"native_version":None,
          "expires_utc":"2026-11-12T11:00:00Z"}
        out=propose([r],approvals(r),l,census(),NOW)
        self.assertEqual(out["state"],"RECONCILE_BEFORE_RECLAIM")

    def test_any_native_job_blocks_account(self):
        r=req()
        for field in ("native_active_count","active_assignments"):
            with self.subTest(field=field):
                c=census();c[field]=1
                out=propose([r],approvals(r),ledger(),c,NOW)
                self.assertEqual(out["state"],"HOLD_NATIVE_ACCOUNT_BUSY")

    def test_unknown_or_stale_census_fails_closed(self):
        r=req()
        bad=census(); bad["status"]="UNKNOWN"
        with self.assertRaisesRegex(ValueError,"CENSUS_SCHEMA"):
            propose([r],approvals(r),ledger(),bad,NOW)
        bad=census(); bad["observed_utc"]="2026-11-12T11:30:00Z"
        with self.assertRaisesRegex(ValueError,"CENSUS_STALE"):
            propose([r],approvals(r),ledger(),bad,NOW)
        bad=census(); bad["observed_utc"]="2026-11-12T12:01:00Z"
        with self.assertRaisesRegex(ValueError,"CENSUS_STALE"):
            propose([r],approvals(r),ledger(),bad,NOW)

    def test_insufficient_kaggle_quota_does_not_admit(self):
        r=req(); c=census();c["quota_remaining_seconds"]["gpu-t4x2"]=899
        self.assertEqual(propose([r],approvals(r),ledger(),c,NOW)["state"],
                         "NO_ELIGIBLE_APPROVED_REQUEST")
        c["quota_remaining_seconds"]["gpu-t4x2"]=None
        with self.assertRaisesRegex(ValueError,"QUOTA_UNKNOWN"):
            propose([r],approvals(r),ledger(),c,NOW)

    def test_gpu_balance_cannot_authorize_tpu_queue(self):
        r=req()
        r["resource"]="tpu-v5e8"
        c=census()
        c["quota_remaining_seconds"]={"gpu-t4x2":12000,"tpu-v5e8":100}
        self.assertEqual(propose([r],approvals(r),ledger(),c,NOW)["state"],
                         "NO_ELIGIBLE_APPROVED_REQUEST")
        c["quota_remaining_seconds"]["tpu-v5e8"]=900
        self.assertEqual(propose([r],approvals(r),ledger(),c,NOW)["state"],
                         "ADMISSIBLE_NOT_CLAIMED")

    def test_kaggle_cpu_requires_no_accelerator_quota(self):
        r=req()
        r["resource"]="cpu"
        c=census()
        c["quota_remaining_seconds"]={"gpu-t4x2":0,"tpu-v5e8":0}
        self.assertEqual(propose([r],approvals(r),ledger(),c,NOW)["state"],
                         "ADMISSIBLE_NOT_CLAIMED")

    def test_insufficient_colab_balance_does_not_admit(self):
        r=req(provider="colab");c=census("colab");c["balance_ccu"]=22
        self.assertEqual(
            propose([r],approvals(r),ledger("colab"),c,NOW)["state"],
            "NO_ELIGIBLE_APPROVED_REQUEST")
        c["balance_ccu"]=None
        with self.assertRaisesRegex(ValueError,"METER_UNKNOWN"):
            propose([r],approvals(r),ledger("colab"),c,NOW)

    def test_approval_scope_blocks_other_provider_or_account(self):
        kaggle=req()
        colab=req("study-colab",provider="colab")
        self.assertEqual(propose([colab],approvals(colab),ledger(),census(),NOW)["state"],
                         "NO_ELIGIBLE_APPROVED_REQUEST")
        foreign=req("foreign-study");foreign["account_alias"]="other-account-001"
        self.assertEqual(propose([foreign],approvals(foreign),ledger(),census(),NOW)["state"],
                         "NO_ELIGIBLE_APPROVED_REQUEST")

    def test_corrupt_lease_fence_blocks(self):
        r=req(); l=ledger()
        l["lease"]={
          "request_id":"study-previous","request_sha256":HASH,
          "fence":2,"native_state":"RUNNING","native_ref":None,
          "native_version":None,"expires_utc":"2026-11-12T12:30:00Z"}
        with self.assertRaisesRegex(ValueError,"LEASE_FENCE"):
            propose([r],approvals(r),l,census(),NOW)

    def test_duplicate_request_id_is_denied(self):
        a=req(); b=copy.deepcopy(a); b["priority"]=2
        with self.assertRaisesRegex(ValueError,"DUPLICATE_REQUEST"):
            propose([a,b],approvals(a),ledger(),census(),NOW)

    def test_duplicate_approval_is_denied(self):
        a=req()
        with self.assertRaisesRegex(ValueError,"DUPLICATE_APPROVAL"):
            propose([a],approvals(a,a),ledger(),census(),NOW)

    def test_request_path_and_budget_safety(self):
        for bad in ("../secrets.json","cloud/../../secrets.json","/tmp/file.json"):
            with self.subTest(bad=bad):
                r=req();r["manifest_path"]=bad
                with self.assertRaisesRegex(ValueError,"MANIFEST_PATH"):
                    validate_request(r)
        r=req();r["max_runtime_seconds"]=3601
        with self.assertRaisesRegex(ValueError,"RUNTIME_INVALID"):
            validate_request(r)
        r=req(provider="colab");r["max_ccu"]=200
        with self.assertRaisesRegex(ValueError,"BUDGET_INVALID"):
            validate_request(r)

    def test_fenced_lease_candidate_requires_cas_and_is_not_persisted(self):
        r=req(); l=ledger(); p=propose([r],approvals(r),l,census(),NOW)
        new=prepare_fenced_claim(l,p,NOW)
        self.assertEqual(new["lease"]["native_state"],"RESERVED")
        self.assertEqual(new["lease"]["fence"],4)
        self.assertEqual(new["lease"]["request_sha256"],digest(r))
        self.assertEqual(l["generation"],3)
        self.assertIsNone(l["lease"])
        self.assertEqual(new["lease"]["expires_utc"],"2026-11-12T12:05:00Z")

    def test_second_caller_cannot_reuse_stale_lease_proposal(self):
        r=req(); original=ledger()
        proposal=propose([r],approvals(r),original,census(),NOW)
        first=prepare_fenced_claim(original,proposal,NOW)
        with self.assertRaisesRegex(ValueError,"LEASE_ALREADY_PRESENT"):
            prepare_fenced_claim(first,proposal,NOW)
        changed=ledger();changed["generation"]=10
        with self.assertRaisesRegex(ValueError,"LEDGER_STALE"):
            prepare_fenced_claim(changed,proposal,NOW)

    def test_forged_claim_token_and_generation_refused(self):
        r=req(); l=ledger(); p=propose([r],approvals(r),l,census(),NOW)
        forged=copy.deepcopy(p);forged["claim_token_sha256"]="0"*64
        with self.assertRaisesRegex(ValueError,"TOKEN_INVALID"):
            prepare_fenced_claim(l,forged,NOW)
        forged=copy.deepcopy(p);forged["proposed_fence"]=5
        with self.assertRaisesRegex(ValueError,"FENCE_INVALID"):
            prepare_fenced_claim(l,forged,NOW)

    def test_native_busy_proposal_cannot_be_rendered_as_lease(self):
        r=req();c=census();c["native_active_count"]=1;l=ledger()
        p=propose([r],approvals(r),l,c,NOW)
        with self.assertRaisesRegex(ValueError,"NOT_ADMISSIBLE"):
            prepare_fenced_claim(l,p,NOW)

    def test_future_request_does_not_enter_queue(self):
        r=req();r["submitted_utc"]="2026-11-12T18:00:00Z"
        self.assertEqual(propose([r],approvals(r),ledger(),census(),NOW)["state"],
                         "NO_ELIGIBLE_APPROVED_REQUEST")

    def test_strict_git_json_reader_rejects_ambiguous_and_nonfinite(self):
        # No provider, Git, credential or actual ledger write is used.
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "input.json"
            for raw, reason in (
                (b'{"schema":"v1","schema":"override"}', "JSON_DUPLICATE_KEY"),
                (b'{"scope":{"enabled":false,"enabled":true}}', "JSON_DUPLICATE_KEY"),
                (b'{"quota":NaN}', "JSON_NONFINITE_NUMBER"),
                (b'{"quota":Infinity}', "JSON_NONFINITE_NUMBER"),
                (b'{"one":1} {"two":2}', "Extra data"),
                (b'{"field":', "Expecting value"),
            ):
                with self.subTest(raw=raw):
                    path.write_bytes(raw)
                    with self.assertRaisesRegex(ValueError, reason):
                        read_strict_json(path)
            path.write_bytes(b'{"scope":{"enabled":false},"quota":0}')
            self.assertEqual(read_strict_json(path),
                             {"scope":{"enabled":False},"quota":0})

    def test_strict_git_json_reader_bounds_untrusted_input(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "too-large.json"
            path.write_bytes(b" " * (1024 * 1024 + 1))
            with self.assertRaisesRegex(ValueError, "INPUT_TOO_LARGE"):
                read_strict_json(path)

if __name__=="__main__":
    unittest.main()
