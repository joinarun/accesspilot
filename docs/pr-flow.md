# Pull-request flow

The PR writer produces only metadata: branch, commit message, title, body, base branch, draft flag, WCAG references, changed pages/files, before/after counts, tests, remaining issues, and limitations.

The UI then shows original issues, applied fixes, remaining issues, verification checks, changed files, diff fingerprint, and the proposed PR. The reviewer chooses approve, reject, or revise.

- **Reject** ends the graph and performs no remote action.
- **Revise** invalidates approval-derived preview/PR state and returns to repair
  while a configured round remains. The next patch consumes that remaining
  round; revise cannot bypass the bound.
- **Approve** is accepted only after deterministic verification passes and
  creates an `ApprovalRecord` bound to changed-file content, verification, PR
  metadata, and the current HEAD.

The managed target application is stopped before the graph waits for this
decision. Static screenshots and AccessPilot-served demo/fixed files remain
available, but a dynamic app URL may no longer be live at review time.

When `execute_pull_request=false` (the default), approval remains a dry run.
Remote publication requires both `execute_pull_request=true` for the run and
`ACCESSPILOT_ENABLE_REMOTE_MUTATIONS=true` on the server. The UI keeps its
remote checkbox disabled until `/api/config` reports that server switch.
Only a run cloned from a reviewed GitHub repository URL has the required
`remote_url`; local-folder and local-Git targets remain non-publishing. The
working branch is created or selected locally before patching so it is part of
the state reviewed at the gate.

Immediately before publication, the guarded service requires passing
verification and rechecks the approval's content, verification, and PR-metadata
fingerprints; live file hashes; reviewed before-content at the approved HEAD;
the repository origin; current HEAD; and the absence of extra staged,
working-tree, or untracked paths. After staging it verifies the exact index path
set and blob hashes. After committing it verifies the parent, changed paths,
and committed blobs, then pushes that exact commit SHA without force.

Hooks are disabled for local Git and push operations. `GH_TOKEN` or
`GITHUB_TOKEN` is withheld from branch/index/commit commands and provided only
to push and `gh` network calls in an isolated temporary home. Existing on-disk
Git/`gh` credentials are not reused. An existing PR for the branch is returned
when possible; otherwise AccessPilot creates a draft or ready PR from the
reviewed metadata.

Any source, index, verification, PR-metadata, origin, or HEAD change after
approval invalidates authorization and requires a new review. The sequence is
not transactional: a later push or `gh pr create` failure can leave a local
commit or pushed branch that an operator must inspect and clean up.
