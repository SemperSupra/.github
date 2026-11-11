# External service integrations — public reusable contract v1

Status: CANDIDATE / source-only. This file is generic architecture; it grants no access,
does not configure organization secrets, and does not start provider executions.

## Five-layer contract

1. **Discover**: service API/CLI support, account/license entitlements, quotas, relevant
   no-charge lane, availability semantics, and exact permitted work.
2. **Describe**: versioned nonsecret service manifest; project-owned provider choice,
   inputs, expected scientific output, run budget, and failure policy.
3. **Authorize**: separately protected repository/environment, trusted machine user
   context, or OIDC-workload federation. No public PR ever consumes credentials.
4. **Execute**: bounded typed operation with source SHA and exact provider run identity,
   idempotent admission, explicit cleanup and quota/cost measurement.
5. **Verify and preserve**: native provider receipt, independent result oracle,
   content-addressed manifest, user-approved off-Git storage, DLE reconciliation.

The framework does **not** become a scheduler, API-key broker, cloud account manager,
experiment owner, scientific evaluator, or authorization authority.

## Service matrix: recommended mechanisms, not installed connections

| Service | Public PR | Protected default-branch or trusted host | Credential model | Claim boundary |
| --- | --- | --- | --- | --- |
| Kaggle | Manifest/CLI/API contract only | Fixed notebook submit/observe | Project environment `KAGGLE_API_TOKEN` | Real GPU/TPU allocation and quota require native receipts |
| Colab Google AI subscription | Source/code/CCU parser only | Personal ADC/Colab CLI on a trusted user host | User OAuth/ADC; **not** generic GCP WIF | Subscription balance and future accelerator rate must be observed |
| Google Drive | Manifest and hash-only | Colab native mount or trusted user-authorized rclone/Drive client | User OAuth/Drive file scopes; approved folder | Google Cloud Storage WIF action **does not** upload to personal Drive |
| Apps Script | `clasp` source/lint/test | Trusted `clasp push/pull` and source roundtrip | User OAuth; remote script IDs held privately | Source deployed is not operational acceptance |
| OpenRouter | API schema/static fake tests | Scoped project/workspace inference consumer | Workload key with explicit limits | Free model rate limits do not imply guaranteed free compute |
| OpenAI API | SDK/evaluation contracts | Dedicated project workload | Prefer documented service-account WIF where eligible; else scoped project key | **Separate** API billing and project limits; never use Admin API key for inference |
| Cloudflare | Wrangler build/dry-run, IaC policy | Worker/Pages/R2/DNS-specific deploy/read | Per-product/resource-scoped API token | Avoid all-account production mutators |
| OCI/GCP/GHCR/Docker | Reproducible build and policy tests | Explicit one-resource deploy | OIDC where supported, otherwise narrow scoped credential | No always-on billable resources without authority |
| Hugging Face/model hubs | License/metadata/ref checks | Model download or gated upload | Read-only scoped token for gated artifacts; separate write token if needed | Model weights/license and scientific qualification remain distinct |

For GitHub's **public** organization workflow library, only generic data and synthetic
fixtures may be committed. Estate-specific mappings belong in the existing private
GitHub Ops repository. A workflow referenced across organizations must be pinned
to a reviewed immutable commit and allowed by the consuming org's Actions policy.

### Secret fan-out rule

Do not store every provider secret in a global organization secret set and enable
all repositories. Prefer consumer **environment** secrets with explicit reviewer,
protected default branch and environment name. A reusable workflow can read a
caller-visible environment secret when its job declares that environment;
prefer an explicit secret mapping over blanket `secrets: inherit` when possible.

Use GitHub OIDC only when the actual **target service** trusts and supports its
federation. An OIDC token for GCP or a GCS service account does not unlock a
personal Google Drive, Colab AI Pro credit balance, or Apps Script user scope.

### Data classification and storage

| Output | Recommendation | Authority |
| --- | --- | --- |
| Source, evaluation code, small approved synthetic fixture | Git | Owning project |
| Run manifest, SHA-256 digest, compact verified aggregate, decisions | Git/DLE | Owning project |
| Stable moderate-size binary with a reason to version as source | Git LFS, after quota/licensing review | Owning project |
| Raw traces, tensor dumps, activation matrices, repeated checkpoints, private data | Private Google Drive | Owning project reference + verified Drive bytes |
| Temporary tool caches and scratch logs | Ephemeral CI/runtime; short-retained GHA if public-safe | No promotion |

Drive is **not** the DLE authority. Each run's trusted-side archive receipt must
include source SHA, experiment ID, exact native provider version, file digests,
sizes, remote identity and observed readback status. A Drive upload success code
without readback cannot establish an accepted artifact. Public GitHub artifacts
should never contain private model traces, hidden benchmarks or private file paths.

### Trusted host publication bridge

The library contains:
- `scripts/cloud/artifact_manifest.py`: streaming SHA-256 manifest builder +
  tamper verifier, classifies Git/LFS/Drive candidates.
- `scripts/cloud/drive_artifact_publish.py`: defaults to **plan-only**;
  trusted host with preapproved user-authorized `rclone` remote invokes
  `--apply` and performs immutable upload + remote readback and SHA-256
  verification; no OAuth material reaches a public GHA runner.
- `scripts/cloud/colab_usage.py`: reads balance/rate/session count from
  already authenticated Colab CLI on trusted host, no VM allocation.

Dry-run example on a host with trusted source checkout and synthetic bundle:

```bash
python3 scripts/cloud/artifact_manifest.py build \
  --root /tmp/study-output --run-id experiment-20261111-a01 \
  --repo example-org/example-repo --source-commit 0000000000000000000000000000000000000000 \
  --classification public-synthetic --output /tmp/artifact-manifest.json

python3 scripts/cloud/artifact_manifest.py verify \
  --root /tmp/study-output --manifest /tmp/artifact-manifest.json \
  --output /tmp/artifact-verify.json

python3 scripts/cloud/drive_artifact_publish.py \
  --root /tmp/study-output --manifest /tmp/artifact-manifest.json \
  --remote-root 'mydrive:Research Experiments/Project' \
  --receipt /tmp/archive-plan.json
```

To perform an **authorized** upload from a trusted host, pass `--apply` to
the last command after verifying the plan, OAuth configuration, budget, and
destination. The initial pilot caps uploads at 100 files and 2 GiB total and
has no automatic retries. Verify remote Drive permissions and rclone's
account-folder binding before the first live upload. Failure can leave
partially uploaded immutable objects; reconcile the run ID before rerunning.

This uploader remains a source-qualified candidate until an actual trusted
host Drive round-trip and evidence review prove it. The same protocol can
later support additional storage transports such as R2 without changing
scientific result identity.

## References for methodology (not authority to deploy)

- GitHub reusable workflows / environment secrets:
  https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows
- GitHub OIDC:
  https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-cloud-providers
- Kaggle CLI: https://github.com/Kaggle/kaggle-cli
- Google Colab CLI: https://github.com/googlecolab/google-colab-cli
- Google Apps Script clasp: https://developers.google.com/apps-script/guides/clasp
- Google Drive resumable uploads: https://developers.google.com/workspace/drive/api/guides/manage-uploads
- OpenRouter docs: https://openrouter.ai/docs
- OpenAI API identity: https://developers.openai.com/api/docs/guides/api-keys
- Cloudflare Worker authorizations: https://developers.cloudflare.com/workers/authorization/
- Rclone Drive backend: https://rclone.org/drive/
