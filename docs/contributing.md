# Contributing and repository guidance

AccessPilot separates repository conventions, LLM advice, and executable policy.
Contributors should preserve that boundary when adding a rule, framework, or
workflow field.

## Guidance loaded from a target repository

For source-backed runs, AccessPilot reads these files when present:

1. `AGENTS.md`
2. `CONTRIBUTING.md`
3. `.github/CONTRIBUTING.md`
4. `docs/CONTRIBUTING.md`

Only regular non-symlink files are read. Each file contributes at most 8,000
characters and the combined context is capped at 20,000. Guidance is passed as
untrusted text to the investigator, planner, and repairer; it is not executed,
does not grant tools, and cannot expand allowed source roots, editable paths,
commands, network access, or publication policy.

## Specialist contracts

The specialist agents are fresh, tool-free, `single_turn` nodes. Input adapter
nodes in `app/workflow.py` send only role-relevant data: selected issue evidence,
deterministic mappings, bounded source excerpts, repository guidance, or
verification summaries. They do not hand a specialist unrestricted workflow
state or filesystem access.

Outputs in `app/specialists/schemas.py` are strict Pydantic DTOs with extra
fields forbidden. They are distinct from the shared workflow/API DTOs in
`app/schemas.py`. When a contract changes, update the specialist factory,
input/merge and deterministic-fallback nodes, focused tests, and—if public run
state changed—the frontend adapter and types.

## Plans and patches

The planner may group issues that share a root cause and provide an explicit
execution order. `_reconcile_planner_output` ignores unknown issue IDs, keeps
safe deterministic actions that the agent did not consume, and uses mapped
repair paths as its base. A group may additionally authorize an existing,
editable file named in `files` or `test_updates` and tie it to the group's known
issue IDs. Supported source/routing suffixes are HTML/HTM, CSS/SCSS, JS/TS,
Python, JSX/TSX, Vue, and Svelte; explicitly test-like paths are classified as
tests. Missing, protected, secret-like, symlinked, or out-of-workspace paths are
ignored, and the planner cannot create a file.

Deterministic and investigator-proposed source candidates are auto-selected
only when the best confidence is at least 0.6. Lower-confidence candidates can
remain as evidence, but they do not become the automatic repair path.

Repairer unified diffs are advisory until validation. A proposal must:

- target a path already present in the approved plan;
- reference only issue IDs owned by that path's plan actions;
- describe one existing editable file and remain below 200,000 characters;
- avoid create/delete/rename/copy, binary, and submodule operations; and
- pass `git apply --check` before application.

After an accepted proposal, deterministic transformations may apply remaining
supported fixes on actions already marked automatic. Planner-added existing
files are non-automatic and change only through a validated repairer diff.
HTML/CSS support is deliberately narrow. JSX/TSX automation is limited to
supported language, image, label, and heading changes; Vue/Svelte handling
edits template markup conservatively. Unsupported or subjective work must stay
unresolved for review.

Each changed file records before/after SHA-256 values and a unified review diff.
Across repair rounds, review diffs may be concatenated, while the authorization
`diff_sha256` is derived from sorted path/final-content-hash pairs rather than
the literal diff text.

## Checks

Run the deterministic suite and formatting checks before proposing a change:

```bash
uv run pytest -q tests/unit tests/integration
uv run ruff check app tests scripts
uv run ruff format --check app tests scripts
```

Behavioral output belongs in the local/agents-cli eval flows described in the
root README; do not assert nondeterministic response wording in unit tests.
