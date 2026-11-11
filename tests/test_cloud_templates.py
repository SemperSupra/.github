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

if __name__ == "__main__":
    unittest.main()
