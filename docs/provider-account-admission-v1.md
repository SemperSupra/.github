# Provider account admission contract (candidate, offline and fail-closed)

**Status: SOURCE/PLAN QUALIFICATION ONLY.** This does not create a second
dispatch system, repository inbox, scheduler, durable queue, token broker,
provider environment, controller service, or authority for scientific claims.

The canonical project issue/workset and the existing Agent Dispatch
admission model remain the durable authority. This public library owns only
an interchangeable **admission decision protocol**, consumable without
Agent Dispatch or by Agent Dispatch when its admission subsystem is ready.

## Why an accelerator-native queue does not solve it

- **Kaggle** queues submitted notebooks and supports time-/dataset-triggered
  reruns of notebook *identities*. It does not provide an authenticated,
  account-wide project-workset request queue, approval ledger, atomic leases
  across callers, or receipt reconciliation to our GitHub DLE.
- **Colab** exposes sessions, usage, one-shot `colab run` and explicit
  teardown. Its runtime is the *job worker*, not the persistent admissions
  service. Keeping an idle Colab accelerator running as a resource broker
  wastes/risks CCUs and is not part of the design.
- A GHA `schedule` / short `workflow_dispatch` or the existing OCI sidecar
  may **wake** the controller, but must be stateless across invocations.
  GitHub Actions `concurrency: {group: account, queue: max}` can serialize
  invocation *within one control repository*, not across independent repos.
  It does not atomically lock the provider account or supply the project DLE.

## Single logical admission writer per account

```text
Project repo PR / private GitHub DLE --(source-pinned request)-->
    existing Agent Dispatch workset / delegated authority
      |                                     ^
      v                                     |
  [short-lived one-writer controller] -- DLE/natives reconciliation
      |  read exact approved request SHA, provider quota/census
      |  observe all account-wide jobs, including external/manual
      |  choose deterministic queue candidate
      |  GitHub durable state CAS with unique monotonically increasing fence
      |  **only after CAS success**, re-observe provider
      |  idempotent typed submit or explicit HOLD
      +--> Kaggle native queued/active job / version
      +--> Colab user-ADC account one-shot session
```

**There is no provider-resident long-running admission process.** Kaggle
native queueing is retained for already-submitted jobs; Colab admission
should only allocate when a real runtime is needed. Resource scarcity is
handled by leaving approved requests in GitHub native worksets (not
submitting multiple accelerators to compete for capacity).

Use a single GitHub repository (or the existing Agent Dispatch control
service) to issue provider-account fences. Startup may race: two controllers
may calculate the same proposal, but **only the winner of a conditional
compare-and-swap durable update may proceed**. The loser must exit without
provider mutation and re-read the accepted lease. A CAS precondition is
not supplied by `scripts/cloud/admission_contract.py`; trusted controller
implementation and native evidence are future qualification gates.

### Crash/failure cases

| State | Required behavior |
| --- | --- |
| No native session, no active ledger lease, census fresh | Propose one approved candidate; CAS required |
| Two controller invocations race | One succeeds conditional ledger update; other loses, never submits |
| Lease reserved but submit crashed before native ID | Observe account and reconcile before release |
| Kaggle notebook QUEUED/RUNNING | Retain lease, poll status; never submit replacement |
| Colab rate high / balance low / session already running | HOLD, no allocation |
| Ledger lease expired while native state unknown | **Do not steal**; require native reconciliation and new fence |
| Native job exists but ledger is empty (manual or other repo) | HOLD_ORPHAN, establish provider account owner |
| Native terminal result with matching version and hash | Validate oracle, publish artifact, CAS reconcile DLE; then release lease |
| Provider/quota query fails, stale/future data or version mismatch | Fail-closed HOLD; do not assume availability |
| Project request changed after approval | Reject unless newly approved exact SHA |
| GitHub Actions schedule delayed/skipped | Next wake reads durable state; no blind retry or duplicate |

## Source-only interface (v1 candidate)

`scripts/cloud/admission_contract.py` takes five trusted-side, versioned
JSON inputs. These are not uploaded or requested from Kaggle/Colab:

1. `requests`: immutable repo/commit/manifest/experiment ID/provider
   account alias/resource/runtime and cost ceiling.
2. `approvals`: **independent** list of exact `request_id + request_sha256`
   authorized by owning DLE and reviewer; the request cannot self-approve.
3. `ledger`: current generation and unresolved lease for exactly one
   provider/account alias.
4. `census`: native account-wide active-job count, quota or CCU balance,
   reserve and freshness. Status UNKNOWN is not zero.
5. `now-utc`: caller-supplied explicit UTC time (testable, no clock hidden
   inside acceptance).

Output is a deterministic `ADMISSIBLE_NOT_CLAIMED`, `OBSERVE_EXISTING_LEASE`,
`RECONCILE_BEFORE_RECLAIM`, `HOLD_NATIVE_ACCOUNT_BUSY`,
`NO_ELIGIBLE_APPROVED_REQUEST`, or `BLOCKED_INVALID_OR_UNKNOWN` receipt.
A selected proposal includes `expected_ledger_sha256`,
`proposed_fence`, source commit, manifest digest and a claim-token digest.
These are **not GitHub locks** and grant NO provider submission authority.

No credential, notebook API call, GitHub mutator, Drive upload, paid resource
allocation, scheduler, repo discovery or environment installation is
performed by this offline planner.

### Conservative initial envelope

- Per-account concurrency: **one submitted/queued/running job**, inclusive
  of other projects and manual provider sessions.
- Provider quota/census freshness: at most 15 minutes and never future-dated.
- Max runtime: 3600 seconds; Kaggle quota must exceed requested runtime by
  at least 300 seconds. Colab per-request budget <=5 CCUs, with explicit
  account reserve. These are *pilot* bounds, not provider policy limits.
- Fixed provider/resource allowlists; no arbitrary scripts, source branch
  names, shell commands, provider IDs, or public-repo secrets in the request.
- Priority 0..3, then earliest UTC submission time, then lexical request ID;
  approval cannot be carried over to an edited request. No starvation
  mitigation or automatic capacity search in v1.
- No automatic resubmission or free-tier credit farming. Native Kaggle
  queuing remains one job; Colab is on-demand allocation only.

## Where a future controller should run

Prefer existing Agent Dispatch control services on OCI for 24/7 account
admission and cross-project DLE continuity. Its host is not a new source of
truth. During admission development, one **single-control-repository**
GHA scheduled/dispatch job can drive Kaggle read/plan/observe without
long-running Actions minutes, but must use an atomic GitHub/native lease
writer and must not be activated by the source-only proposal PR.

Personal Colab ADC/CCU checks stay on the already-qualified Personal Ops
trusted host. A public hosted GHA runner can create **plans** but cannot
silently inherit personal Google AI Pro entitlements; a trusted user-context
launcher is required for allocation and uploads.

Reference authorities:
- `SemperSupra/agent-dispatch-private#96`, `#101`, `#110`, `#401`, `#485`, `#638`.
- `SemperSupra/latent-reasoning-private#2` for experiment-specific claims.
- `SupraShellScripts/github-ops-lab-private#18` for GitHub environment access.
- Existing shared reusable Kaggle/Colab workflows remain draft PR #2.

## Qualification ladder

1. **Done when CI passes:** offline 24-case state-machine/negative
   acceptance tests, reproducibility of decisions, no provider network.
2. **Not yet done:** owner-reviewed durable conditional-write CAS lease
   integration plus two-start race/interruption test on a single GitHub
   account-control workset.
3. **Not yet done:** account-wide Kaggle scan including foreign manual jobs
   with native version receipts; one quota-bounded submit/observe.
4. **Not yet done:** trusted Colab live CCU/sessions query, conservative
   allocation, shutdown, and observed quota delta.
5. **Not yet done:** exact native results/Drive artifact hash, scientific
   oracle and project-native DLE terminal reconciliation.

No queue admission, cross-account lock, provider dispatch, or autonomous
overnight service is activated by merging this interface alone.

## Provider census adapter evidence

The shared Kaggle `workflow_call` now accepts a protected `census` operation without a job manifest: one read-only account quota query plus native job status enumeration; output carries separate `gpu-t4x2` and `tpu-v5e8` remaining seconds, active-job count and timestamp. It does not reserve resources. Colab `usage` remains restricted to a trusted user-ADC context and emits CCU balance, observed CCU/hour and active assignments. A future controller maps both to the admission schema with the explicit provider-account alias; consumers must not infer identity or quota values. Any uncertain or stale census blocks admission.
