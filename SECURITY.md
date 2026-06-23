# Security

This document records security controls and accepted-risk decisions for Meridian.
The standing rules live in `CLAUDE.md → Security Guardrails`; this file tracks the
*operational* state (CI gates, dependency exceptions). Do not weaken AD-1 (human
approval) or AD-6 (independent eval judge) — they are load-bearing controls.

## Dependency audit (`pip-audit`)

CI runs `pip-audit --strict` (`.gitlab-ci.yml → security` stage). It fails on any
known CVE in the resolved dependency set **except** the documented exceptions
below. The exceptions exist because **no patched release exists** for any of them
(each package is already on its latest version), and each vulnerable code path is
either unreachable or not invoked by Meridian. The ignore-list is a tripwire, not
a mute button: a *new* vulnerability, or a *fix* shipping for one of these, turns
the stage red again.

| CVE | Package (how pulled) | Vulnerability | Why accepted | Reachable in Meridian? |
|-----|----------------------|---------------|--------------|------------------------|
| CVE-2025-3000 | `torch` 2.12.0 (transitive ← sentence-transformers) | Memory corruption in `torch.jit.script`; local-only | We use torch for embedding inference only (`all-MiniLM-L6-v2` forward pass). `torch.jit.script` is never called. Exploit needs local code execution. No fix released. | No |
| CVE-2026-6587 | `ragas` 0.4.3 (direct) | SSRF in the `multi_modal_faithfulness` metric via `retrieved_contexts` | We instantiate only the **text** metrics (`Faithfulness`, `ResponseRelevancy`, `ContextPrecision`, `FactualCorrectness`). The multimodal metric is never constructed, so the path is dormant. No fix released; `ragas>=0.4` cannot downgrade past it. | No (dormant) |
| CVE-2025-69872 | `diskcache` 5.6.3 (transitive) | Pickle deserialization RCE if attacker can write to the cache directory | Requires prior filesystem-write access to the host. App container runs non-root with no exposed cache-write surface. No fix planned (pickle default is by-design). | No |

### Guardrails that keep these dormant (do not violate without re-reviewing the CVE)

- **Do not add a ragas multimodal metric** (`MultiModalFaithfulness` / anything under
  `ragas.metrics.collections.multi_modal_*`) without re-evaluating CVE-2026-6587 — that
  is the only one of the three with a network-shaped (SSRF) vector, and `retrieved_contexts`
  is untrusted input per `CLAUDE.md → Security Guardrails`.
- **Keep torch to embedding inference only** — no `torch.jit.script` / TorchScript compilation.
- **Ship the app container non-root** (Phase 8 container hardening) — shrinks the diskcache path.

### Review cadence

Re-run `pip-audit` (no ignores) periodically and whenever dependencies change:

```bash
.venv/Scripts/python.exe -m pip_audit --strict   # PowerShell host: same, via the venv
```

If a listed CVE gains a fix version, upgrade and remove its `--ignore-vuln` entry from
`.gitlab-ci.yml`. If a new CVE appears, triage it — do not blanket-add it to the ignore-list.

## Secret scanning (`detect-secrets`)

`detect-secrets` runs as a pre-commit hook (`.pre-commit-config.yaml`) and as the CI
`secret_scan` stage. Both compare against `.secrets.baseline`, which captures existing
dev-only literals (docker-compose passwords, the all-zero Langfuse dev encryption key).
A new, unbaselined secret blocks the commit locally and fails CI.

Regenerate the baseline only intentionally:

```bash
.venv/Scripts/python.exe -m detect_secrets scan > .secrets.baseline
```
