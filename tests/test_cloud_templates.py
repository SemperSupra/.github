"""No-credential contract and budget tests for shared cloud templates."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.cloud.cloud_contract import load, plan, digest
from scripts.cloud.colab_usage import parse_usage, evaluate

class CloudTemplatesTests(unittest.TestCase):
    def fixture(self, provider="kaggle"):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "experiments").mkdir()
        (root / "manifests").mkdir()
        src = root / "experiments" / "test_job.py"
        src.write_text("print('synthetic-only')\n")
        m = {
            "schema": "semper-cloud-experiment/v1",
            "experiment_id": "synthetic-contract-01",
            "provider": provider,
            "accelerator": "NvidiaTeslaT4" if provider == "kaggle" else "T4",
            "source": "experiments/test_job.py",
            "source_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
            "max_runtime_seconds": 300,
            "receipt_file": "output/result.json",
            "budget": ({"min_remaining_seconds": 600} if provider == "kaggle"
                       else {"max_ccu": 5, "reserve_ccu": 20,
                             "assumed_max_ccu_per_hour": 10}),
        }
        manifest = root / "manifests" / "test.json"
        manifest.write_text(json.dumps(m))
        return root, manifest, m

    def test_both_provider_plans(self):
        for provider in ("kaggle", "colab"):
            with self.subTest(provider=provider):
                root, path, m = self.fixture(provider)
                loaded = load(path, root, provider)
                outcome = plan(loaded, root, path)
                self.assertEqual(outcome["state"], "PLANNED_NOT_SUBMITTED")
                self.assertEqual(outcome["source_sha256"], m["source_sha256"])
                self.assertNotIn("token", str(outcome).lower())

    def test_manifest_path_outside_workspace_denied(self):
        root, path, m = self.fixture("kaggle")
        outside = root.parent / ("outside-" + root.name + ".json")
        try:
            outside.write_text(json.dumps(m))
            with self.assertRaisesRegex(ValueError, "MANIFEST_NOT_IN_WORKSPACE"):
                load(outside, root, "kaggle")
        finally:
            outside.unlink(missing_ok=True)

    def test_source_change_denied(self):
        root,path,m=self.fixture()
        (root / m["source"]).write_text("print('other')\n")
        with self.assertRaisesRegex(ValueError, "SOURCE_HASH_MISMATCH"):
            load(path, root, "kaggle")

    def test_unsafe_relative_path_denied(self):
        root,path,m=self.fixture()
        m["source"] = "experiments/../../credentials.json"
        path.write_text(json.dumps(m))
        with self.assertRaisesRegex(ValueError, "INVALID_RELATIVE_PATH"):
            load(path, root, "kaggle")

    def test_provider_and_accelerator_denied(self):
        root,path,m=self.fixture("colab")
        m["accelerator"] = "TpuV5E8"
        path.write_text(json.dumps(m))
        with self.assertRaisesRegex(ValueError, "ACCELERATOR_NOT_ALLOWLISTED"):
            load(path, root, "colab")
        m["accelerator"] = "T4"
        path.write_text(json.dumps(m))
        with self.assertRaisesRegex(ValueError, "PROVIDER_SCHEMA_MISMATCH"):
            load(path, root, "kaggle")

    def test_budget_not_enough(self):
        root,path,m=self.fixture("colab")
        m["budget"]["max_ccu"] = 0.1
        path.write_text(json.dumps(m))
        with self.assertRaisesRegex(ValueError, "COLAB_BUDGET_GATE_INVALID"):
            load(path, root, "colab")

    def test_unknown_field_denied(self):
        root,path,m=self.fixture()
        m["enable_internet"] = True
        path.write_text(json.dumps(m))
        with self.assertRaisesRegex(ValueError, "MANIFEST_FIELDS_INVALID"):
            load(path, root, "kaggle")

    def test_usage_parse_and_eligible(self):
        raw = "Current balance: 399.07049082 compute units\nUsage rate: 0.0/hr\nActive assignments: 0\n"
        x = parse_usage(raw)
        self.assertEqual(x["balance_ccu"],399.07049082)
        b={"max_ccu": 5, "reserve_ccu": 20, "assumed_max_ccu_per_hour": 10}
        y=evaluate(x,b)
        self.assertEqual(y["admission"],"BUDGET_PRECHECK_PASS_NOT_RESOURCE_RESERVED")
        self.assertIn("No prospective accelerator",y["prediction_boundary"])

    def test_active_session_denied(self):
        x={"balance_ccu":400.0,"rate_ccu_per_hour":0.0,"active_assignments":1}
        b={"max_ccu":5.0,"reserve_ccu":20,"assumed_max_ccu_per_hour":10}
        self.assertEqual(evaluate(x,b)["admission"],"BLOCKED")

    def test_low_balance_denied(self):
        x={"balance_ccu":21.0,"rate_ccu_per_hour":0.0,"active_assignments":0}
        b={"max_ccu":5.0,"reserve_ccu":20,"assumed_max_ccu_per_hour":10}
        self.assertEqual(evaluate(x,b)["admission"],"BLOCKED")

    def test_unparseable_usage_denied(self):
        with self.assertRaisesRegex(ValueError,"COLAB_USAGE_SCHEMA_CHANGED"):
            parse_usage("Usage available: unknown")
        with self.assertRaisesRegex(ValueError,"COLAB_USAGE_SCHEMA_CHANGED"):
            parse_usage("Current balance: 5 compute units\nUsage rate: 0/hr\nActive assignments: 0\nActive assignments: 1")

    def test_consumer_examples_do_not_inherit_all_secrets(self):
        root=Path(__file__).resolve().parents[1]
        documentation=(root/"docs/cloud-workflows-v1.md").read_text()
        self.assertNotIn("    secrets: inherit",documentation)
        self.assertIn("protected kaggle-research environment",documentation)

    def test_template_boundaries(self):
        root=Path(__file__).resolve().parents[1]
        kg=(root/".github/workflows/reusable-kaggle-v1.yml").read_text()
        cb=(root/".github/workflows/reusable-colab-v1.yml").read_text()
        self.assertIn("workflow_call:",kg)
        self.assertIn("environment: kaggle-research",kg)
        self.assertIn("github.ref == 'refs/heads/main'",kg)
        self.assertIn("github.event_name != 'pull_request'",kg)
        self.assertIn("job.workflow_sha",kg)
        self.assertIn("workflow_call:",cb)
        self.assertIn("self-hosted",cb)
        self.assertIn("colab-adc",cb)
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS:",cb)
        self.assertNotIn("KAGGLE_API_TOKEN:",cb)


class DriveArtifactTests(unittest.TestCase):
    def fixture(self):
        from scripts.cloud.artifact_manifest import build
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root=Path(temporary.name)
        (root/"payload").mkdir()
        (root/"payload"/"metrics.json").write_text('{"status":"PASS"}')
        (root/"payload"/"checkpoint.bin").write_bytes(b"public synthetic checkpoint"*1024)
        manifest=build(root/"payload","exp-20261111-a01",
                       "SemperSupra/latent-reasoning","a"*40,"private-research")
        return root/"payload",manifest

    def test_private_artifacts_route_to_drive(self):
        from scripts.cloud.artifact_manifest import verify
        root,manifest=self.fixture()
        self.assertEqual({v["recommended_storage"] for v in manifest["files"]},{"drive"})
        result=verify(root,manifest)
        self.assertEqual(result["status"],"VERIFIED_LOCAL_NOT_UPLOADED")
        self.assertEqual(result["file_count"],2)

    def test_manifest_fails_on_modified_payload(self):
        from scripts.cloud.artifact_manifest import verify
        root,manifest=self.fixture()
        (root/"metrics.json").write_text("TAMPERED")
        with self.assertRaisesRegex(ValueError,"FILE_SIZE_MISMATCH|FILE_HASH_MISMATCH"):
            verify(root,manifest)

    def test_git_candidate_for_public_small_json(self):
        from scripts.cloud.artifact_manifest import build
        root,manifest=self.fixture()
        public=build(root,"exp-20261111-a01",
                     "SemperSupra/latent-reasoning","a"*40,"public-synthetic")
        routes={v["relative_path"]:v["recommended_storage"] for v in public["files"]}
        self.assertEqual(routes["metrics.json"],"git-candidate")
        self.assertEqual(routes["checkpoint.bin"],"drive")

    def test_drive_remote_rejects_traversal(self):
        from scripts.cloud.drive_artifact_publish import sanitized_remote_prefix
        for unsafe in ("gdrive:/root","gdrive:../escape","gdrive:folder/../../escape",
                       "gdrive:foo//bar","gdrive:"):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(ValueError):
                    sanitized_remote_prefix(unsafe)

    def test_drive_selection_is_fixed(self):
        from scripts.cloud.drive_artifact_publish import selection, remote_for
        root,manifest=self.fixture()
        rows=selection(manifest)
        self.assertEqual(len(rows),2)
        self.assertEqual(
            remote_for("drive:SemperSupra/Research Experiments/Latent Reasoning",
                       manifest,"checkpoint.bin"),
            "drive:SemperSupra/Research Experiments/Latent Reasoning/exp-20261111-a01/checkpoint.bin")

    def test_drive_immutable_upload_readback(self):
        from unittest.mock import patch
        from scripts.cloud.drive_artifact_publish import upload_and_verify
        root,manifest=self.fixture()
        rows=[r for r in manifest["files"] if r["relative_path"]=="metrics.json"]
        seen=[]
        def fake(argv, out=None):
            seen.append(argv)
            if argv[1]=="cat":
                out.write((root/"metrics.json").read_bytes())
        with patch("scripts.cloud.drive_artifact_publish.invoke",side_effect=fake):
            result=upload_and_verify(root,manifest,"drive:SemperSupra/Research Experiments",rows)
        self.assertTrue(result[0]["readback_verified"])
        self.assertEqual(seen[0][0:3],["rclone","copyto","--immutable"])
        self.assertEqual(seen[1][0:2],["rclone","cat"])

    def test_drive_corrupt_readback_denied(self):
        from unittest.mock import patch
        from scripts.cloud.drive_artifact_publish import upload_and_verify
        root,manifest=self.fixture()
        rows=[r for r in manifest["files"] if r["relative_path"]=="metrics.json"]
        def fake(argv,out=None):
            if argv[1]=="cat":
                out.write(b"CORRUPTED")
        with patch("scripts.cloud.drive_artifact_publish.invoke",side_effect=fake):
            with self.assertRaisesRegex(ValueError,"REMOTE_HASH_MISMATCH|REMOTE_SIZE_MISMATCH"):
                upload_and_verify(root,manifest,"drive:SemperSupra/Research Experiments",rows)


class ColabExecutionGuardTests(unittest.TestCase):
    def fixture(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root=Path(temp.name)
        (root/"experiments").mkdir()
        (root/"experiments"/"job.py").write_text("print('hello')\n")
        manifest={"experiment_id":"colab-guard-001",
                  "accelerator":"T4",
                  "max_runtime_seconds":900,
                  "source":"experiments/job.py",
                  "source_sha256":"a"*64,
                  "receipt_file":"output/result.json",
                  "budget":{"max_ccu":5,"reserve_ccu":20,
                            "assumed_max_ccu_per_hour":12}}
        return root, manifest

    def test_post_allocation_rate_above_ceiling_stops_without_exec(self):
        from unittest.mock import patch
        from scripts.cloud import colab_provider as provider
        root,m=self.fixture()
        calls=[]
        def fake_cli(args,timeout):
            calls.append(args[0])
        with patch.object(provider,"collect",return_value={
                "admission":"BUDGET_PRECHECK_PASS_NOT_RESOURCE_RESERVED",
                "balance_ccu":100.0}), patch.object(provider,"read_snapshot",
                return_value={"active_assignments":1,"rate_ccu_per_hour":25.0}
                ), patch.object(provider,"cli",side_effect=fake_cli):
            with self.assertRaisesRegex(ValueError,"EXECUTION_OR_CLEANUP"):
                provider.execute(m,root,root)
        self.assertEqual(calls,["new","stop"])

    def test_zero_gpu_rate_is_unqualified_not_free_allocation_proof(self):
        from unittest.mock import patch
        from scripts.cloud import colab_provider as provider
        root,m=self.fixture()
        calls=[]
        with patch.object(provider,"collect",return_value={
                "admission":"BUDGET_PRECHECK_PASS_NOT_RESOURCE_RESERVED",
                "balance_ccu":100.0}), patch.object(provider,"read_snapshot",
                return_value={"active_assignments":1,"rate_ccu_per_hour":0.0}
                ), patch.object(provider,"cli",
                side_effect=lambda args,timeout:calls.append(args[0])):
            with self.assertRaises(ValueError):
                provider.execute(m,root,root)
        self.assertEqual(calls,["new","stop"])

    def test_bounded_colab_path_retrieves_receipt_and_stops(self):
        from unittest.mock import patch
        from scripts.cloud import colab_provider as provider
        root,m=self.fixture()
        calls=[]
        snapshots=iter([
            {"admission":"BUDGET_PRECHECK_PASS_NOT_RESOURCE_RESERVED",
             "balance_ccu":100.0},
            {"admission":"BUDGET_PRECHECK_PASS_NOT_RESOURCE_RESERVED",
             "balance_ccu":98.7,"active_assignments":0},
        ])
        def fake_cli(args,timeout):
            calls.append(args[0])
            if args[0]=="download":
                Path(args[-1]).write_text('{"status":"SYNTHETIC"}')
        with patch.object(provider,"collect",side_effect=lambda budget:next(snapshots)),\
             patch.object(provider,"read_snapshot",
                 return_value={"active_assignments":1,"rate_ccu_per_hour":3.0}),\
             patch.object(provider,"cli",side_effect=fake_cli):
            outcome=provider.execute(m,root,root)
        self.assertEqual(calls,["new","exec","download","stop"])
        self.assertEqual(outcome["observed_ccu_delta"],1.3)
        self.assertTrue(outcome["known_session_stopped"])
        self.assertEqual(outcome["projected_runtime_ccu"],.75)
        self.assertEqual(outcome["entitlement_scope"],"PAID_CCU_ONLY_NOT_FREE_GPU_QUOTA")

    def test_meter_unknown_never_accepts_execution(self):
        from unittest.mock import patch
        from scripts.cloud import colab_provider as provider
        root,m=self.fixture()
        calls=[]
        def fake_cli(args,timeout):
            calls.append(args[0])
            if args[0]=="download":
                Path(args[-1]).write_text("{}")
        with patch.object(provider,"collect",side_effect=[
            {"admission":"BUDGET_PRECHECK_PASS_NOT_RESOURCE_RESERVED","balance_ccu":100.0},
            ValueError("account rate unavailable")]),\
            patch.object(provider,"read_snapshot",
                return_value={"active_assignments":1,"rate_ccu_per_hour":3.0}),\
            patch.object(provider,"cli",side_effect=fake_cli):
            with self.assertRaisesRegex(ValueError,"EXECUTION_OR_CLEANUP_OR_METER"):
                provider.execute(m,root,root)
        self.assertEqual(calls,["new","exec","download","stop"])

    def test_colab_stop_success_but_active_assignment_denied(self):
        from unittest.mock import patch
        from scripts.cloud import colab_provider as provider
        root,m=self.fixture()
        snapshots=iter([
            {"admission":"BUDGET_PRECHECK_PASS_NOT_RESOURCE_RESERVED",
             "balance_ccu":100.0,"active_assignments":0},
            {"admission":"BLOCKED","balance_ccu":99.5,"active_assignments":1},
        ])
        def fake_cli(args,timeout):
            if args[0]=="download":
                Path(args[-1]).write_text('{"status":"SYNTHETIC"}')
        with patch.object(provider,"collect",side_effect=lambda budget:next(snapshots)),\
             patch.object(provider,"read_snapshot",
                 return_value={"active_assignments":1,"rate_ccu_per_hour":2.0}),\
             patch.object(provider,"cli",side_effect=fake_cli):
            with self.assertRaisesRegex(ValueError,"STILL_ACTIVE"):
                provider.execute(m,root,root)

    def test_racing_second_session_aborts_and_attempts_cleanup(self):
        from unittest.mock import patch
        from scripts.cloud import colab_provider as provider
        root,m=self.fixture()
        observed=[]
        def fake_cli(args,timeout):
            observed.append(args[0])
        with patch.object(provider,"collect",return_value={
                "admission":"BUDGET_PRECHECK_PASS_NOT_RESOURCE_RESERVED",
                "balance_ccu":100.0}),\
             patch.object(provider,"read_snapshot",
                return_value={"active_assignments":2,"rate_ccu_per_hour":5.0}),\
             patch.object(provider,"cli",side_effect=fake_cli):
            with self.assertRaisesRegex(ValueError,"EXECUTION_OR_CLEANUP"):
                provider.execute(m,root,root)
        self.assertEqual(observed,["new","stop"])


class KaggleSharedAccountGuardsTests(unittest.TestCase):
    def test_other_namespace_running_job_blocks(self):
        from types import SimpleNamespace
        from scripts.cloud.kaggle_provider import no_duplicate_or_active
        for other in ("user/latent-c001-tpu-bridge-v1",
                      "user/ad-rdte-gpu-t4x2", "user/other-job"):
            with self.subTest(other=other):
                class Fake:
                    def kernels_list_with_response(self,**kwargs):
                        return SimpleNamespace(kernels=[SimpleNamespace(ref=other)],
                                               next_page_token=None)
                    def kernels_status(self,ref):
                        return SimpleNamespace(status="RUNNING")
                with self.assertRaisesRegex(ValueError,"ACCOUNT_JOB_ACTIVE"):
                    no_duplicate_or_active(Fake(),"user/semper-new")

    def test_other_namespace_unknown_fails_closed(self):
        from types import SimpleNamespace
        from scripts.cloud.kaggle_provider import no_duplicate_or_active
        class Fake:
            def kernels_list_with_response(self,**kwargs):
                return SimpleNamespace(kernels=[
                    SimpleNamespace(ref="user/some-foreign-job")],next_page_token=None)
            def kernels_status(self,ref):
                return SimpleNamespace(status="MYSTERIOUS")
        with self.assertRaisesRegex(ValueError,"ACCOUNT_JOB_UNKNOWN"):
            no_duplicate_or_active(Fake(),"user/semper-new")

    def test_finished_foreign_job_not_blocked(self):
        from types import SimpleNamespace
        from scripts.cloud.kaggle_provider import no_duplicate_or_active
        class Fake:
            def kernels_list_with_response(self,**kwargs):
                return SimpleNamespace(kernels=[
                    SimpleNamespace(ref="user/old-project")],next_page_token=None)
            def kernels_status(self,ref):
                return SimpleNamespace(status="COMPLETE")
        self.assertIsNone(no_duplicate_or_active(Fake(),"user/semper-new"))

    def test_target_duplicate_blocked_even_when_terminal(self):
        from types import SimpleNamespace
        from scripts.cloud.kaggle_provider import no_duplicate_or_active
        class Fake:
            def kernels_list_with_response(self,**kwargs):
                return SimpleNamespace(kernels=[
                    SimpleNamespace(ref="user/semper-new")],next_page_token=None)
        with self.assertRaisesRegex(ValueError,"TARGET_ALREADY_EXISTS"):
            no_duplicate_or_active(Fake(),"user/semper-new")

    def test_staged_source_sha_mismatch_blocks_before_push(self):
        from types import SimpleNamespace
        from scripts.cloud.kaggle_provider import submit
        from datetime import timedelta
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"experiments").mkdir()
            (root/"experiments"/"job.py").write_text("print('safe')\n")
            m={"accelerator":"NvidiaTeslaT4",
               "budget":{"min_remaining_seconds":600},
               "source":"experiments/job.py",
               "source_sha256":"0"*64,
               "experiment_id":"staged-source-guard", "max_runtime_seconds":300}
            class Fake:
                CONFIG_NAME_USER="username"
                push_called=False
                def authenticate(self): pass
                def get_config_value(self,name): return "user"
                def quota_view(self): return SimpleNamespace(
                    gpu_quota=SimpleNamespace(total_time_allowed=timedelta(seconds=3600),
                                              time_used=timedelta(seconds=0)))
                def kernels_list_with_response(self,**kwargs):
                    return SimpleNamespace(kernels=[],next_page_token=None)
                def kernels_push(self,*a,**k):
                    self.push_called=True
            api=Fake()
            with self.assertRaisesRegex(ValueError,"SOURCE_HASH_CHANGED"):
                submit(api,m,root)
            self.assertFalse(api.push_called)


if __name__ == "__main__":
    unittest.main()
