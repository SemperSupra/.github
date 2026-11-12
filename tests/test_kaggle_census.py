"""Synthetic, no-auth census tests for the Kaggle account-wide reader."""
from datetime import timedelta
from types import SimpleNamespace
import unittest

from scripts.cloud.kaggle_provider import account_census, kernel_status, no_duplicate_or_active

def quota(gpu_allowed=3600, gpu_used=600,
          tpu_allowed=2400, tpu_used=120):
    def slot(allowed,used):
        return SimpleNamespace(total_time_allowed=timedelta(seconds=allowed),
                               time_used=timedelta(seconds=used))
    return SimpleNamespace(gpu_quota=slot(gpu_allowed,gpu_used),
                           tpu_quota=slot(tpu_allowed,tpu_used))

class Fake:
    def __init__(self, statuses, quota_obj=None, next_tokens=None):
        self.statuses=statuses
        self.calls=[]
        self._quota=quota_obj or quota()
        self.next_tokens=next_tokens or [None]
        self.authenticated=False
    def authenticate(self):
        self.authenticated=True
    def quota_view(self):
        self.calls.append("quota_view")
        return self._quota
    def kernels_list_with_response(self, **kwargs):
        self.calls.append("kernels_list_with_response")
        next_token=self.next_tokens.pop(0) if self.next_tokens else None
        return SimpleNamespace(kernels=[
            SimpleNamespace(ref=x) for x in self.statuses
        ],next_page_token=next_token)
    def kernels_status(self,ref):
        self.calls.append("kernels_status")
        return SimpleNamespace(status=self.statuses[ref])
    def kernels_push(self,*a,**kw):
        raise AssertionError("GET-only census must never submit")

class KaggleAccountCensusTests(unittest.TestCase):
    def test_separate_quota_pools_and_foreign_native_busy(self):
        api=Fake({"user/manual-work":"RUNNING","user/old-study":"COMPLETE"})
        r=account_census(api)
        self.assertEqual(r["state"],"OBSERVED")
        self.assertTrue(api.authenticated)
        self.assertEqual(r["quota_remaining_seconds"],
                         {"gpu-t4x2":3000,"tpu-v5e8":2280})
        self.assertEqual(r["native_active_count"],1)
        self.assertEqual(r["kernels_examined"],2)
        self.assertNotIn("manual-work",str(r))
        self.assertNotIn("user/",str(r))
        self.assertNotIn("kernels_push",api.calls)

    def test_known_terminal_account_is_idle(self):
        api=Fake({"user/recent":"SUCCESS","user/failed":"ERROR"})
        self.assertEqual(account_census(api)["native_active_count"],0)

    def test_unknown_native_state_does_not_mean_available(self):
        api=Fake({"user/foreign":"SOMETHING_NEW"})
        with self.assertRaisesRegex(ValueError,"STATUS_UNKNOWN"):
            account_census(api)

    def test_unknown_gpu_or_tpu_quota_blocks_entire_census(self):
        q=quota()
        q.tpu_quota=None
        with self.assertRaisesRegex(ValueError,"ACCOUNT_QUOTA_UNKNOWN"):
            account_census(Fake({},quota_obj=q))

    def test_invalid_quota_debit_denied(self):
        with self.assertRaisesRegex(ValueError,"ACCOUNT_QUOTA_INVALID"):
            account_census(Fake({},quota_obj=quota(gpu_allowed=100,gpu_used=120)))

    def test_incomplete_pagination_denied(self):
        api=Fake({},next_tokens=["page1"]*20)
        with self.assertRaisesRegex(ValueError,"PAGE_LOOP"):
            account_census(api)

    def test_census_requires_no_notebook_manifest_or_submission(self):
        from pathlib import Path
        workflow=Path(__file__).resolve().parents[1]/".github/workflows/reusable-kaggle-v1.yml"
        yaml=workflow.read_text()
        self.assertIn('plan|submit|observe|census',yaml)
        self.assertIn('if: inputs.operation != \'census\'',yaml)
        self.assertIn('--action census --output',yaml)
        self.assertIn("github.event_name != 'pull_request'",yaml)
        self.assertIn("environment: kaggle-research",yaml)

    def test_false_terminal_substring_does_not_mark_account_idle(self):
        for value in ("NOT_COMPLETE", "INCOMPLETE", "NOT_SUCCESS",
                      "UNKNOWN_CANCEL", "STILL_RUNNING_UNKNOWN"):
            with self.subTest(value=value):
                api = Fake({"example/foreign":value})
                with self.assertRaisesRegex(ValueError, "ACCOUNT_KERNEL_STATUS_UNKNOWN"):
                    account_census(api)
                self.assertNotIn("kernels_push", api.calls)

    def test_no_duplicate_or_active_rejects_false_terminal(self):
        api = Fake({"example/foreign":"NOT_COMPLETE"})
        with self.assertRaisesRegex(ValueError, "OTHER_KAGGLE_ACCOUNT_JOB_UNKNOWN"):
            no_duplicate_or_active(api, "example/new-study")

    def test_native_known_enum_values_are_exact(self):
        self.assertEqual(kernel_status("KernelStatus.QUEUED"), "QUEUED")
        self.assertEqual(kernel_status("KernelStatus.COMPLETE"), "COMPLETE")
        self.assertEqual(kernel_status("COMPLETED"), "COMPLETED")
        with self.assertRaisesRegex(ValueError, "KERNEL_STATUS_UNKNOWN"):
            kernel_status("CustomState.COMPLETE")
        api = Fake({"example/foreign":"KernelStatus.RUNNING"})
        self.assertEqual(account_census(api)["native_active_count"],1)

if __name__ == "__main__":
    unittest.main()
