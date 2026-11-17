# Android Publishing Foundry — Durable, Legible, Executable plan
Date: 2026-11-17 | Status: **proposal, not implemented** | Authority: user-requested planning only

## 1. Intent and release contract

Build reusable, free-tier GitHub-based Android open-source application packaging/distribution for five delivery surfaces: (1) official F-Droid, (2) self-hosted F-Droid repository, (3) Amazon Appstore (Fire OS), (4) Google Play, (5) direct signed APK via GitHub Releases. **All production promotion requires explicit human approval.** Amazon Vega OS VPKGs are a future separate adapter, not an APK target.

The reusable foundry belongs in a **new, dedicated public** `SemperSupra/android-publishing-foundry` repo; this organization-level proposal is the bootstrap anchor only. No such dedicated repository was found on 2026-11-17. Do not expand the charters of model-artifact-foundry, agent-dispatch, or android-artifact-recovery to own distribution.

An entirely zero-dollar *cloud execution* design is achievable with public repos and standard hosted Linux GitHub Actions runners. **Google Play's $25 one-time registration is a nonzero external account prerequisite** if no existing developer account exists; it cannot be bypassed by GHA. Store approval, developer identity and compatible devices are other external gates. No spend is authorized by this plan.

## 2. D — durable contract and decisions

- One FLOSS core; optional Gradle product flavors `fdroid`, `direct`, `amazon`, `play` only if binaries actually diverge. Never include proprietary SDKs in official F-Droid build. Common Kotlin/Java code, no mandatory Play Services; optional purchase/login/telemetry adapters only in eligible variants.
- Separate source revision, release candidate, approval decision, signed distribution artifact, submitted app version, live store listing states. Idempotent transition `source -> build -> qualify -> candidate -> owner_approved -> submitted -> in_review -> live | rejected`. Only explicit owner approval with candidate SHA256, cert fingerprint, variant, store destinations and versionCode allows publication.
- Immutable provenance: exact Git commit SHA, dependency/toolchain digests, SBOM, APK/AAB SHA256, signing certificate fingerprint, versionName/versionCode, test/evidence report and submission/receipt identifier. Tags and URLs are locators, not sufficient identities.
- Google Play upload key differs from Play App Signing app-signing key; F-Droid may re-sign its own builds unless reproducible binary signature reuse is qualified. Cross-store in-place updates are **not guaranteed**. Record a per-pair {applicationId, app-signing cert, versionCode, channel} update matrix before first production release. Do not mix version codes unintentionally.
- Non-privileged untrusted PR CI: read-only token; no cloud credentials, store credentials, release signing keys or write permission. No untrusted code under privileged `pull_request_target`; pin third-party Actions full commit SHA; explicit permissions; tightly scoped environments with manual reviewers. Avoid production signing keys on general public GitHub runners unless threat model and owner's written approval allow it. Prefer owner-controlled offline signing/approval initially.
- No proprietary assets, disallowed licensing, unsafe trackers, unreviewed remotely fetched build scripts, sensitive personal data or private-provider evidence in public repo/artifacts. Negative policy evidence fails closed.
- Three audiences: human documentation, machine-readable JSON schema/CLI, agent AGENTS.md/DLE; independently runnable in clean CI without private GitHub credentials.
- Free-tier discipline: standard public GitHub-hosted Ubuntu runner; concurrency limit 1 per release target; timeout and artifact retention limits; no paid runners or paid API integrations; bounded emulator matrix; no uncontrolled self-hosted runners. Document storage/quota and abort rather than accrue costs.
- Store privacy policy, support contact, content rating, data safety disclosures, export compliance and app ownership are mandatory inputs; no automatically invented claims or owner identity.

## 3. L — dedicated repository contract

```text
AGENTS.md                        # actor operating contract and authority boundaries
CURRENT_TASK.md                  # bounded active rep and next actor baton
README.md                        # human quickstart / release policy
docs/architecture/
  distribution-matrix.md         # app types, store compatibility, metadata
  signing-updates.md             # cert matrix, key custody, rollback/forward fix
  trust-threat-model.md          # PR/run, supply chain, signing, owner gates
docs/decisions/ADR-0001-*.md
docs/plans/{mvp-critical-path,roadmap,owner-gates}.md
schemas/{app-manifest,qualification-evidence,release-receipt}.schema.json
examples/hello-distribution/     # small original FLOSS Kotlin Android app
tools/                           # Go-first manifest validator, hash, release plan CLI
fdroid/{official,self-hosted}/    # build recipe, metadata, signed repo index tooling
adapters/{amazon,play}/          # dry-run first, mock transports, later gated API
tests/{schema,negative,fixture,security}/
.github/workflows/{verify,build,release-candidate,publish-approved}.yml
```

Consumer contract: `publisher.yaml` or JSON manifest identifies app ID, version, min/target SDK, supported ABI/device modes, license, disclosures, source provenance, expected signing fingerprint, artifact types, channel config, owner-approval mode. Validate with schema and semantic checks prior to any effect. Shared reusable workflows or pinned tags may run in consumer repos; never allow PR from untrusted fork to access publishing secrets.

## 4. E — prioritized waves and acceptance tests

### W0 (P0): Authority, governance, schema, compatibility [no credentials]
- Discover actual GitHub runner census, repo rules and existing workflows before consuming CI quota.
- Current official store policy/SDK/API versions, license eligibility, Android target API and Fire OS capabilities captured with dated evidence.
- Create manifest schema, release state machine, threat model, cert/update compatibility matrix, baseline Android app identity policy, 20+ negative fixtures, signing owner gate, rejection path. Distinguish free Android build infrastructure from unavoidable Play Console fee.
- **Acceptance:** schema/semantic validator returns stable errors for 20 negative fixtures; no accidental publication possibility; owner can interpret proposed source→release state transitions.
- **Parallel worksets:** A contracts, B store policy fact matrix, C threat model, D test fixture corpus.

### W1 (P0): FLOSS app + unprivileged verification
- Pin Gradle wrapper distribution+checksum, AGP, JDK, Android SDK/NDK as needed; dependency verification and licenses.
- Implement small original sample Android app with zero mandatory network permission. Make channel variants only where needed. Build Android `assembleFdroidRelease`, `assembleDirectRelease`, `bundlePlayRelease`, `assembleAmazonRelease` or document if flavors can be shared and tasks differ.
- CI: formatting, unit tests, Android lint, dependency/license/secret scanning, dependency graph, SBOM (SPDX), artifact hash, emulator smoke test on available standard Ubuntu runners.
- **Acceptance:** clean independent source builds, no F-Droid-disallowed SDK/dependency, 0 privileged PR writes, stable evidence receipts, signed test install/start/uninstall in emulator. Do not claim physical Fire OS coverage from generic emulator.

### W2 (P0): Reproducible signed direct APK and self-hosted F-Droid
- Owner-created production app-signing key, stored offline by default with documented backup, restoration and renewal process. Separate nonproduction key. Dry-run no-signing or test key CI.
- Two-build deterministic comparison and source→APK reproducibility evidence; F-Droid reproducible signature reuse qualified separately. Official F-Droid inclusion does not universally mandate reproducible builds.
- Test Android update from versionCode N to N+1 using same cert; fail on cert mismatch, downgrade, package mismatch. Check exported components, permissions, manifest, minSDK, ABI.
- Create signed APK, checksums, SBOM, attestation, changelog and signed F-Droid repo index. Publish to GitHub Pages under free limits only after verified owner gate, plus direct APK GitHub Release. Ensure private/test repo index never confused with production.
- **Acceptance/MVP:** *one* truly installable and updateable release through direct APK and self-hosted F-Droid, manual production gate and no paid cloud charges; if no real device, mark actual-hardware acceptance blocked and don't claim completion.

### W3 (P1): Official F-Droid
- Build official fdroiddata proposal/recipe, compile from FLOSS source with `fdroid lint` and `fdroid build`; supply anti-features where applicable, source archive, license and screenshot metadata.
- Submit official inclusion request after owner approval; observe review/rejections, reconcile release IDs. Official F-Droid independently determines signing mode and review acceptance.
- **Acceptance:** listing verified live, otherwise status explicitly `submitted`, `in review` or `blocked`.

### W4 (P1): Amazon Appstore and Google Play adapter
- Amazon: owner identity verification (required based on 2026-09/10/11 notices), console initial app registration and API credentials scoped to needed capabilities. App Submission API supports follow-on versions but not every initial-console step. Upload APK Fire OS variant, stage submission, poll, reconcile external state.
- Play: check existing account; if absent $25 owner-authorized fee before production possible; registration and identity/device/testing requirements by account type; owner creates Play API service account and app. Build upload-key-signed AAB for Play App Signing. APIs support test tracks and edits; do not promote production without exact digest-specific owner approval.
- Contract mock-server tests for auth failures, retries/429, partial uploads, version conflicts, policy rejection, idempotent duplicate requests. Never retry destructive actions blindly.
- **Acceptance:** successful authorized internal/closed test release + recorded receipt per available store; independent production owner approval; public availability only with live listing proof. Never assume account privileges.

### W5 (P2): Multi-app/ops/maintenance and optional new platforms
- Manifest-driven catalog, dependency update PRs, signed release rotation, vulnerability advisories, telemetry-free failure summaries, update compatibility and partial rollback/forward-fix documents.
- Automated deterministic critics plus optional bounded LLM/VLM agents for accessibility, screenshot/store listing review and risk triage; critics cannot override release gates. Record exact model/version, cost/limit, input evidence, verdict/falsifier and human adjudication.
- Vega OS is separate non-Android VPKG frontend with independent build ecosystem and compatibility test; defer.

## 5. MVP critical path and dependency gates

`W0 contracts -> W1 testable FLOSS artifact -> W2 signing / provenance / reproducibility / update test -> G1 human authorization -> direct APK + self-hosted F-Droid live pilot`.

Official F-Droid review, Amazon Appstore production and Google Play production are *parallel post-MVP P1* distribution tracks; no need to block core pipeline on store onboarding. W4 mock/test adapters can run alongside W1–W2, but **never** acquire production credentials before gated authorization.

Owner gates:
- G0: confirm intended first app and original license, package/application ID and repository creation authority (can build sample without approval).
- G1: key custody, public APK/index location, privacy/asset clearance, exact signed hash and SHA, manual approve public direct release.
- G2: official F-Droid inclusion request approval.
- G3: Amazon identity and API grant (manual).
- G4: Google developer registration payment/verification (manual if required).
- G5: **per-app, per-version, per-channel production approval**, with evidence bundle and expiry; never global or implicit.
Rollbacks: pull the unpublished candidate; signed Android app versionCode cannot safely decrease; store delisting is not automatic rollback—prepare forward fix with higher versionCode.

## 6. Qualification evidence and critic matrix

Deterministic mandatory gates: lint, unit tests, build checks, OSS licenses, dependency verification, secrets/SAST, manifest exported components/permissions, signature fingerprint, min/target SDK, versionCode monotonicity, SBOM, 2-build reproducibility check, APK vs AAB package identity, actual update path for chosen signing channel, F-Droid anti-feature checks, hash-chain and release-manifest schema; negatives exercise intentionally bad keys, signature, licenses, API token, package IDs, mismatched hashes, stale versions and untrusted PR workflows. Optional bounded critics (never authority) inspect accessibility, privacy language, TV remote focus navigation and visual defects. Hardware/device evidence and store acceptance are separate proof classes from emulators and model critics.

Evidence JSON fields: `source_commit`, `workflow_run`, `toolchain`, `artifact_sha256`, `signing_cert_sha256`, `application_id`, `version_code`, `channel`, `qualified_tests`, `known_gaps`, `reviewer`, `approval_id`, `submission_id`, `store_status`, `timestamp`, `cost_class`.

## 7. Worksets, hygiene and cross-repository reconciliation

Independent bounded branches/PRs:
A contracts/schema and 20 negative fixtures; B sample Android Gradle/CI; C signing/reproducibility/update matrix; D self-hosted F-Droid index dry run; E Amazon/Play mock adapters; F governance/security test harness. Each reports commit SHA, test logs, proved vs unproved claims, blockers, and next rep. Don't merge unreviewed work. Keep `AGENTS.md`, `CURRENT_TASK.md`, roadmap and issues reconciled; prefer Go for deployable orchestration tooling, Kotlin for Android. Public runner workload must remain free and bounded.

Existing repo boundaries observed:
- `SemperSupra/model-artifact-foundry`: existing `AGENTS.md` defines public-only credentials, digest-pinned artifact approvals, no untrusted privileged PR jobs. **Reuse patterns, do not publish Android builds there.**
- `SemperSupra/agent-dispatch`: orchestration and bounded free-tier actors, no Appstore credentials.
- `SemperSupra/au-companion`: its README establishes sanitized public release airlock; only consider as later consumer after private authority approves promotion.
- `SemperSupra/BrowserParity`: potential later device/browser qualification consumer, not the first app.
- `SemperSupra/android-artifact-recovery`: adjacent recovery expertise, no automatic publishing authority.
- `SemperSupra/.github`: bootstrap organization-wide DLE proposal, not implementation repository.
- Existing private repo material is not automatically portable to public CI.

## 8. Local actor cold-start contract
"Identify actor/model/effort, UTC clock, OS/arch, cwd, repo/branch/commit, token/context and CI quotas. Read this plan, `AGENTS.md` of any target repo, and current GitHub issues/PRs. Follow Observe→Plan→Act→Verify in bounded independent worksets. Do not spend money, create secrets, bypass required manual approvals, merge or publish. First prepare dedicated public repository proposal or report creation blocker. Implement W0 contract/schema/negative fixtures and safe dry-run validation in reviewable PR; evidence must include commands, green/negative tests, commit SHA and next baton."

## 9. Current authoritative references (checked 2026-11-17)
- F-Droid inclusion: https://fdroid.gitlab.io/jekyll-fdroid/en/docs/Inclusion_Policy/
- F-Droid source submission: https://fdroid.gitlab.io/jekyll-fdroid/docs/Submitting_to_F-Droid_Quick_Start_Guide/
- F-Droid reproducible signature verification: https://fdroid.gitlab.io/jekyll-fdroid/docs/Reproducible_Builds/
- F-Droid private repo setup: https://fdroid.gitlab.io/jekyll-fdroid/en/docs/Setup_an_F-Droid_App_Repo/
- Amazon App Submission API: https://developer.amazon.com/docs/app-submission-api/overview.html
- Amazon identity verification: https://developer.amazon.com/docs/app-submission/identity-verification.html
- Play Console $25: https://support.google.com/googleplay/android-developer/answer/6112435
- Play signing: https://developer.android.com/studio/publish/app-signing
- GHA public standard runner eligibility: https://docs.github.com/en/billing/concepts/product-billing/github-actions

## 10. Status ledger at initial publication
W0 **specified but not executed**, W1–W5 **not started**. No APK built; no CI configured; no signing keys generated; no Amazon/Play API creds touched; no store submission attempted; no free-tier job dispatched. This document is a reviewable plan only.
