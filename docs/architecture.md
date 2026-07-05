# Architecture

AccessPilot separates probabilistic judgment from deterministic side effects.

## Layers

1. **ADK graph** — `app/workflow.py` defines the explicit topology, route maps, retry policy, repair back-edge, final report, and resumable human input.
2. **Specialists** — `app/specialists/` creates fresh, tool-free,
   `single_turn` agents for audit interpretation, source investigation,
   planning, repair proposals, verification assessment, and PR writing. Each
   receives a minimal role-specific input DTO rather than the full workflow
   state, and every output is a strict `extra="forbid"` Pydantic schema.
3. **Policy services** — `app/services/` owns workspace isolation, process supervision, framework/route detection, scanning, normalization, source mapping, patching, verification, previews, and Git/PR operations.
4. **Provider abstraction** — `app/providers/` returns an ADK model for native Gemini or LiteLLM configured only for an enterprise LLMproxy.
5. **Serving** — the scaffolded ADK API and custom FastAPI routes share the same process; `app/run_manager.py` keeps resumable runner/session objects for UI runs.
6. **UI** — `frontend/` provides intake, status, filters, plan, previews, verification, diff/test summary, and approval controls.

Large screenshots and runtime workspaces remain on disk under ignored `.accesspilot/` roots. Workflow state contains paths and structured summaries rather than binary payloads.

## Repository guidance and repair contracts

AccessPilot reads a bounded set of repository conventions from `AGENTS.md`,
`CONTRIBUTING.md`, `.github/CONTRIBUTING.md`, and `docs/CONTRIBUTING.md`.
Symlinks are ignored; each file is capped at 8,000 characters and the combined
context at 20,000. This text is untrusted context for the investigator, planner,
and repairer only. It cannot grant tools, change an allowed source path, or
relax deterministic policy.

The source mapper auto-selects a candidate only at confidence 0.6 or above;
lower-confidence deterministic or investigator suggestions remain evidence,
not automatic path ownership. The agent planner may group related issues and
order dependencies. Reconciliation ignores unknown issue IDs, preserves safe
unconsumed deterministic actions, and may add an existing editable source,
routing, or test file explicitly named by a group and tied to known issues. It
cannot create a file or escape the editable-path policy.

The repairer sees only plan-owned files. Its unified diff must reference one
existing approved path, contain only approved issue IDs, stay below the size
limit, avoid create/delete/rename/copy, binary, and submodule operations, and
pass `git apply --check` before use. Planner-added files are non-automatic and
can change only through such a validated diff.

The deterministic fallback then applies a narrow set of HTML/CSS and component
transformations. JSX/TSX handling is limited to supported language, image,
label, and heading rules; Vue/Svelte changes are confined to template markup.
Unsupported or subjective framework changes remain unresolved for human work.
Every resulting `FileChange` records before/after hashes and a review diff.

## Trust boundaries

- Repository content and rendered pages are untrusted data.
- Model output is advisory and schema-constrained.
- Only policy services may touch files, launch processes, or invoke Git/GitHub.
- Only `PullRequestNode`, reached through a matching `ApprovalRecord`, can call the remote-changing path.
- Request flags cannot raise the server's local-execution, dependency-install,
  or remote-mutation ceilings.

## Runtime persistence

The prototype uses in-memory ADK sessions and run records. A restart loses run
state. Graceful FastAPI shutdown stops all managed child processes, and a
managed local application is explicitly stopped before the workflow waits for
human review. Isolated workspaces and artifacts remain ignored on disk for
inspection. Production should use a durable session database, artifact object
storage, job queue, and per-run OS sandbox.
