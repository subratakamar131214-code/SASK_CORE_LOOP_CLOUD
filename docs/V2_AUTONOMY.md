# SASK v2.0 — Full-Lifecycle Autonomy Upgrade

Status: implemented on branch `v2.0-autonomy`; the v1.0 tree (`v1.0` branch) is
the frozen empirical baseline. No v1.0 kernel file is modified by this work:
`verify.py`, `scale_orchestrator.py`, `novelty_vault.py`, the sealed suites, and
`verify_integrity.json` are consumed read-only.

## 1. Design constraints and how they are met

| Constraint | Implementation |
| --- | --- |
| Zero paid spend | Local providers (Ollama HTTP) first, free-tier OpenAI-compatible endpoints next, offline distilled-rule provider last. Keys come from environment variables only. No paid SDK, no billing path. |
| Immutable kernel + out-of-band root | Zone policy + SHA-256 manifest + per-process audit hook + opt-in OS read-only seal. The supervisor is a separate process pinned to the manifest. |
| Public-safe IP quarantine | `ip_quarantine/` and `artifacts/private_candidates/` are git-ignored and writable only through a module-private `QuarantineWriter` capability. Knowledge is AES-256-GCM at rest, decrypted in memory only. A2UI messages carry hashes/scores, never artifact bodies. |
| No speculative bloat | Every module is exercised by `tests_v2/`; no module exists without a measurable behavior in the telemetry/digest surface. |

## 2. Architecture map

```
                     ┌──────────────────────────────────────────────┐
                     │ Out-of-band supervisor (separate process)    │
                     │ sask_v2/supervisor.py                        │
                     │ heartbeat · RSS · telemetry rotation ·       │
                     │ boot-loop rollback request · self-hash pin   │
                     └───────────────┬──────────────────────────────┘
                                     │ reads/writes state only
┌────────────────────────────────────┴─────────────────────────────────────┐
│ Immutable kernel (read-only to autonomous code)                          │
│ verify.py · scale_orchestrator.py · novelty_vault.py · sealed suites ·   │
│ sask_v2/boundary.py · sask_v2/supervisor.py · kernel_manifest.json       │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │ read-only imports
┌───────────────────────────────┴──────────────────────────────────────────┐
│ Mutable outer ring (sask_v2/)                                            │
│                                                                          │
│  curriculum ──► oracles ──► abstraction ──► knowledge (encrypted)        │
│      ▲              │             │                ▲                     │
│      │              ▼             ▼                │                     │
│   feeds          repair ─────► compute ◄──────────┘                     │
│  (read-only)   (ring patches)  (provider mesh)                          │
│                                                                          │
│  evidence (bundles) · hud (read-only digest server)                      │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │ capability-gated writes only
                     ┌──────────┴───────────┐
                     │ Private quarantine   │
                     │ ip_quarantine/       │
                     │ (git-ignored, sealed)│
                     └──────────────────────┘
```

Data flow per cycle: signals (counterexamples, transfers, frontier stalls) →
`curriculum` emits falsifiable goals → `oracles` synthesizes and runs a harness
→ `abstraction` generalizes successful rewrites → proven rules land in the
encrypted `knowledge` store → `compute` allocates slots and rotates providers →
`repair` heals mutable-ring adapters → verified discoveries are quarantined,
bundled by `evidence`, and surfaced by `hud`.

## 3. Boundary and permission table

| Zone | Paths | Who may write | Enforcement |
| --- | --- | --- | --- |
| Out-of-band supervisor | `sask_v2/supervisor.py`, `sask_v2/boundary.py` | Nobody at runtime; edited only by a maintainer, then re-pinned | Manifest hash pin (`check_pin`), audit hook, read-only zone resolution |
| Immutable kernel | `verify.py`, `scale_orchestrator.py`, `novelty_vault.py`, `verify_integrity.json`, `verify_holdout_suite.json`, `verify_golden_suite.json`, `sask_v2/boundary.py`, `sask_v2/supervisor.py` | Maintainer only, out of band | `Boundary.assert_mutable` denial, audit hook (`open`/`os.*`), `KernelGuard.seal_os` read-only bit / Windows read-only attribute, manifest drift detection |
| Mutable outer ring | `sask_v2/ring/`, `sask_v2/templates/`, `sask_v2/adapters/` | Repair engine and autonomous patches, after a regression probe | `Boundary.assert_mutable` allows only these prefixes; patch must compile and pass the caller's regression predicate before the atomic write |
| Private quarantine | `ip_quarantine/`, `artifacts/private_candidates/` | Only code holding a `QuarantineWriter` capability in the current thread | Capability token is module-private; audit hook rejects writes without an active capability; directories are git-ignored |
| Everything else | repo root, `docs/`, untracked UI stack | Normal development | Not writable through v2 APIs; `assert_mutable` reports it `unclassified` and denies |

Enforcement is intentionally layered and honest:

1. **Cooperative policy** — all v2 writers call `assert_mutable` or
   `quarantine_writer`; there is no alternate write path in the package.
2. **Runtime audit hook** — `install_audit_hook(root)` raises
   `BoundaryViolation` (a `PermissionError`) for write-mode `open`, `os.remove`,
   `os.rmdir`, `os.mkdir`, `os.chmod`, `os.rename`, `os.replace`,
   `os.truncate`, `shutil.copyfile`, and `shutil.move` targeting kernel files or
   the quarantine. Hooks are per-process, so each autonomous Python process
   must install one at startup; `python -m sask_v2.supervisor --guard` does this
   for the supervisor itself.
3. **OS read-only seal** — `KernelGuard.seal_os()` clears write bits
   (POSIX) or sets the read-only attribute (Windows). This blocks writers in
   unrelated processes. It is opt-in (`freeze(harden=True)`), because it also
   blocks a maintainer running as the same OS user, who must `thaw_os()` first.
4. **Manifest drift** — `KernelGuard.freeze()` records SHA-256 digests;
   `drift()` and `assert_intact()` detect any out-of-band edit, and
   `check_pin()` refuses to start a supervisor whose source no longer matches.

**Limits (explicit):** a process running as the same OS user can always chmod,
kill, or replace its own files, and a Python process that never installs the
audit hook is not covered by it. True privilege separation requires launching
the supervisor (and ideally the worker) as a different OS user or service
account. The v2 layer provides defense in depth, not an adversary-proof jail
against the owning account.

## 4. Subsystems → modules → tests

| # | Subsystem | Module(s) | Tests |
| --- | --- | --- | --- |
| 1 | Self-regulating goal & curriculum engine | `sask_v2/curriculum.py` | `tests_v2/test_curriculum.py` |
| 2 | Verification synthesis & recursive meta-verification | `sask_v2/oracles.py` (static audit, harness synthesis, differential consensus, multi-axis promotion), bridging read-only to `verify.py` / `devil_advocate.py` | `tests_v2/test_oracles.py` |
| 3 | Abstraction synthesis & multi-encoding routing | `sask_v2/abstraction.py` | `tests_v2/test_abstraction.py` |
| 4 | Sandboxed self-modification, crash-to-patch repair, auto-rollback | `sask_v2/repair.py` + `sask_v2/boundary.py` | `tests_v2/test_repair.py`, `tests_v2/test_boundary.py` |
| 5 | Empirical ingestion & encrypted compounding knowledge | `sask_v2/feeds.py`, `sask_v2/knowledge.py` | `tests_v2/test_feeds.py`, `tests_v2/test_knowledge.py` |
| 6 | Compute meta-control, mesh failover, out-of-band supervisor | `sask_v2/compute.py`, `sask_v2/supervisor.py` | `tests_v2/test_compute.py`, `tests_v2/test_supervisor.py` |
| 7 | Filing-ready quarantine bundles & read-only observer digest | `sask_v2/evidence.py`, `sask_v2/hud.py` | `tests_v2/test_evidence_hud.py` |

Key end-to-end proofs in the suite:

- a sealed kernel file rejects a write from a **child process** (OS boundary);
- the audit hook blocks direct kernel and quarantine writes but allows an
  active `QuarantineWriter`;
- `KernelGuard` detects out-of-band edits and refuses to start a drifted
  supervisor;
- a loophole candidate (right signature, wrong behavior) is rejected by a
  synthesized harness; a candidate that diverges from solver consensus is not
  promoted; single-axis gains are rejected;
- repeated failing patches are blacklisted and never touch disk; a clean tree
  rolls back behind a recovery branch only when explicitly enabled;
- rate limits, deprecation, connection loss, and an all-providers-down case
  heal via rotation, backoff, retirement, and offline distillation;
- a hung worker is killed from a stale heartbeat; telemetry rotation and
  boot-loop rollback requests work without human input;
- quarantine bundles are hash-consistent and their A2UI copies contain no raw
  artifact; the HUD serves GET/HEAD only and returns 405 for every mutating
  method.

## 5. Runbook

All commands assume the repository root and the local venv.

```bash
# full v2 suite (104 tests)
.venv/Scripts/python.exe -m sask_v2.selftest

# freeze the kernel manifest; add harden=True to also seal files read-only
.venv/Scripts/python.exe -c "from sask_v2.boundary import KernelGuard; g=KernelGuard('.'); print(g.freeze()); print(g.describe())"

# restore owner write access after harden=True
.venv/Scripts/python.exe -c "from sask_v2.boundary import KernelGuard; print(KernelGuard('.').thaw_os())"

# pin check / one tick / continuous supervisor
.venv/Scripts/python.exe -c "from sask_v2.supervisor import check_pin; print(check_pin('.'))"
.venv/Scripts/python.exe -m sask_v2.supervisor --root . --once --skip-pin
.venv/Scripts/python.exe -m sask_v2.supervisor --root . --guard --interval 5 --allow-rollback --tag v1.0

# read-only observer HUD on http://127.0.0.1:8420/
.venv/Scripts/python.exe -m sask_v2.hud --port 8420
```

Operational notes:

- **Vault key.** Set `SASK_VAULT_KEY` (at least 16 characters) in the process
  environment or CI secret. Without it the knowledge store is an in-memory
  scratch pad and never reports a durable save. A wrong key leaves the store
  `locked` and it refuses to overwrite the existing blob.
- **Provider keys.** Supply free-tier keys through environment variables read
  at call time (for example the `env_key` chosen when constructing an
  `OpenAICompatProvider`). No key is written to disk or logged. With no
  network or keys, the mesh degrades to the offline distilled provider.
- **Rollback.** `rollback_to_tag(..., allow=True)` requires a clean worktree,
  verifies the tag, creates `sask-pre-rollback-<epoch>`, and only then performs
  `git reset --hard`. The supervisor only attempts this with
  `--allow-rollback`.
- **Feeds.** Only HTTPS URLs on the allowlist are fetched. Cached payloads are
  content-addressed under the private quarantine; delete
  `ip_quarantine/datasets/index.json` to force re-download.
- **Manifest discipline.** `sask_v2/kernel_manifest.json` is written only by a
  maintainer freeze. Re-freeze after any intentional kernel or supervisor
  change, otherwise `check_pin` and `assert_intact` will report drift.
- **Process sessions.** Spawn workers with `start_new_session=True` on POSIX.
  `_kill_tree` signals a process group only when the worker pid leads it, so a
  worker that shares the runner's group can never cause the supervisor to kill
  the CI job itself.

## 5.1 Continuous integration

`.github/workflows/v2_autonomy.yml` runs on pushes and pull requests targeting
`v2.0-autonomy` (plus manual dispatch). It installs only `cryptography`, then
byte-compiles `sask_v2`/`tests_v2` and runs `python -m sask_v2.selftest`
followed by a direct `tests_v2` discovery run on `ubuntu-latest`. It does not
trigger on `main`, so the v1.0 baseline workflows are unchanged.

## 6. Public-safety invariants

- `ip_quarantine/`, `artifacts/private_candidates/`, and `sask_v2/state/` are
  git-ignored.
- Quarantined artifacts, decrypted vault state, evidence bundles, and keys
  never enter the public repository or stdout.
- A2UI novelty alerts contain only SHA-256 digests, scores, tier counts, and
  timings.
- The HUD serves a digest document, not files; unknown paths return 404 and all
  mutating verbs return 405 with `Cache-Control: no-store` and a deny-all CSP.
