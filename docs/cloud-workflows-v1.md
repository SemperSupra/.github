# SemperSupra shared cloud research workflows (v1 candidate)

**Status: DRAFT / no consumer has been migrated or given execution authority.**

These are reusable GitHub Actions **workflows**, not a new scheduler, job
controller, Agent Dispatch admission bypass or token broker. Public standard
GitHub runners perform source/manifest preflight. Kaggle uses a protected
environment secret; Colab uses only an existing, dedicated trusted ADC host.

## Interfaces

- `.github/workflows/reusable-kaggle-v1.yml` supports `plan`, `submit`,
  `observe` (requires exact provider-native version). `submit`/`observe` only
  on the caller's protected `main` with `kaggle-research` environment.
- `.github/workflows/reusable-colab-v1.yml` supports `plan` on free public GHA
  and `usage` / `run` only on an explicitly approved self-hosted
  `[self-hosted,Linux,X64,colab-adc]` runner in `colab-research` environment.
  **Do not attach this runner to an untrusted public PR repository.** Prefer
  the already-qualified Personal Ops WSL/ADC one-shot launcher until a
  dedicated runner group restricted to trusted workflows is approved.
- Provider logic is locked to the same workflow revision by
  `job.workflow_repository` and `job.workflow_sha`. Consumers should pin the
  workflow `uses:` dependency to a reviewed immutable commit SHA (not `main`).
- Every consumer has its own source-hashed reviewed manifest; no arbitrary
  `script=...` input, use of PR secrets, output identity guessing or default
  internet-enabled Kaggle notebook.

## Manifest v1

Store e.g. `cloud/manifests/bridge-smoke.json` on the consumer main branch:

```json
{
  "schema": "semper-cloud-experiment/v1",
  "experiment_id": "latent-bridge-smoke-01",
  "provider": "colab",
  "accelerator": "T4",
  "source": "experiments/bridge_smoke.py",
  "source_sha256": "<exact 64-character SHA-256 of reviewed source>",
  "max_runtime_seconds": 900,
  "receipt_file": "output/result.json",
  "budget": {
    "max_ccu": 5,
    "reserve_ccu": 20,
    "assumed_max_ccu_per_hour": 15
  }
}
```

Accepted values are deliberately narrow: Kaggle `cpu`, `NvidiaTeslaT4`,
`TpuV5E8`; Colab `cpu`, `T4`, `L4`, `A100`, `v5e1`, `v6e1`.
Source must be a non-symlink Python file inside `experiments/` and less
than 256 KiB. Receipt path is under `output/`; source SHA must match exactly.
Kaggle budget is `min_remaining_seconds` and must exceed the runtime
limit by 300 seconds. Colab budget fields shown above are required.

## Caller workflow examples

Once a stable shared SHA is approved, add to consumer repository:

```yaml
name: bounded-colab
on:
  workflow_dispatch:
    inputs:
      action:
        type: choice
        options: [plan, usage, run]
        default: plan
jobs:
  colab:
    permissions:
      contents: read
    uses: SemperSupra/.github/.github/workflows/reusable-colab-v1.yml@<APPROVED_SHA>
    with:
      manifest_path: cloud/manifests/bridge-smoke.json
      operation: ${{ inputs.action }}
```

Kaggle variant:

```yaml
name: bounded-kaggle
on:
  workflow_dispatch:
    inputs:
      action:
        type: choice
        options: [plan, submit, observe]
        default: plan
      version:
        type: number
        default: 0
jobs:
  kaggle:
    permissions:
      contents: read
    uses: SemperSupra/.github/.github/workflows/reusable-kaggle-v1.yml@<APPROVED_SHA>
    with:
      manifest_path: cloud/manifests/gpu-smoke.json
      operation: ${{ inputs.action }}
      native_version: ${{ inputs.version }}
    secrets: inherit
```

**Environment secrets are held in each consuming repository.**
For Kaggle, configure protected environment `kaggle-research` with
`KAGGLE_API_TOKEN`, allowed branch `main`, and required reviewer if appropriate.
Do not pass Google OAuth refresh tokens, ADC files or browser cookies as GitHub
secrets. Personal Google AI Pro Colab is **not** equivalent to an ordinary
machine-identity GCP service account; a service-account WIF token cannot
be assumed to provide the user's subscription CCUs.

## Colab usage and accounting

On the already-qualified trusted WSL host in Personal Ops:

```bash
colab --auth=adc usage
colab --auth=adc sessions
```

Or run the shared sanitized sensor (after reviewing and placing code on that
trusted host; it does not start a Colab VM):

```bash
python3 scripts/cloud/colab_usage.py \
  --budget cloud/manifests/bridge-smoke.json \
  --output /tmp/colab-usage-sanitized.json
```

The sensor returns current **remaining** balance, aggregate hourly
consumption and active assignment count; it cannot establish future
accelerator price, remaining subscription calendar entitlement or guaranteed
availability. It blocks new work when any assignment is already active,
balance is below reserve plus per-job budget or the provider is unreachable.
Configured `assumed_max_ccu_per_hour` is a conservative operator envelope,
not provider pricing. After run, query balance again; record CCU delta,
and repeat later if billing settlement is delayed.

## Security and evidence

- No auto top-ups, paid GCP fallback, unbounded retries, GPU quota speculation,
  public PR secret access, remote arbitrary commands or session keep-alives.
- Kaggle provider adapts official Python SDK; actual provider operations remain
  **unqualified until native receipt and the exact-version API semantics pass**.
  `observe` only reports platform state. Consumer science verifier must inspect
  output and independently validate model/task success.
- Colab one-shot creates a unique named session, sends the reviewed source,
  downloads the bounded receipt and explicitly stops it. Failure to stop or
  observe metering is not acceptance. A timeout can create cleanup ambiguity;
  manually reconcile active sessions and CCU balance before another run.
- The core manifests and receipts are usable by human CLI, automation and
  agents. Full runtime logs, private data, and credential material never
  become public GHA artifacts.
- An account-level resource lock across *different* GitHub repositories is
  **not guaranteed** by GitHub's per-repository concurrency group. Use a
  single trusted admission owner or external lock if multiple consumers
  begin submitting simultaneously. The v1 provider's active-job probe
  reduces but does not eliminate racing submissions.

## Migration acceptance

1. Merge this shared repo PR only after independent review and CI.
2. Pin immutable workflow commit SHA in a single test consumer; plan-only first.
3. Verify PR plan and main protected run gates/secret resolution.
4. For Kaggle: native quota/status/submit/observe receipt, exact version and
   no duplicate submissions before any science or cross-project migration.
5. For Colab: trusted WSL read-only usage and session census first; do not
   activate self-hosted public-repo runner until its isolation is reviewed.
6. Record DLE: Durable (manifests/receipts), Legible (budget and claims),
   Executable (source-pinned provider-specific one-shot lifecycle).

Original authorities remain:
- `SemperSupra/latent-reasoning-private` Campaign 0001 issue #2;
- `mark-e-deyoung/personal-ops` #90 for Colab local ADC/Drive;
- `SemperSupra/agent-dispatch-private` #401 for future dispatch integration.

Official sources: 
- https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows
- https://github.com/Kaggle/kaggle-cli/blob/main/docs/kernels.md
- https://github.com/googlecolab/google-colab-cli/blob/main/README.md
- https://github.com/googlecolab/google-colab-cli/blob/main/docs/04_automation_and_utility.md