# AccessPilot frontend

Accessible React/TypeScript interface for the local AccessPilot API. Vite proxies
`/api` to `http://127.0.0.1:8000` in development. Set `VITE_API_BASE_URL` only
when the API is hosted elsewhere.

## Commands

```bash
npm install
npm run dev
npm run build
```

No credentials belong in Vite environment variables because client-side values
are visible to the browser. Authentication instructions sent by the form must
contain process guidance only, never passwords, cookies, or tokens.

## API contract

The canonical FastAPI responses use snake-case field names. The adapter also
accepts a legacy `{ "data": ... }` envelope, but the current run snapshot is
`{ "state": {...}, "events": [...] }`.

### `GET /api/config`

```json
{
  "providers": [
    {
      "name": "gemini",
      "configured": true,
      "model": "gemini-2.5-pro",
      "detail": "Native Gemini credentials are configured."
    },
    {
      "name": "openai",
      "configured": false,
      "model": "gpt-4.1",
      "detail": "Configure the approved LLMproxy connection."
    }
  ],
  "default_provider": "gemini",
  "max_repair_rounds": 3,
  "sample_target": "sample://vulnerable-app",
  "local_execution_enabled": false,
  "dependency_install_enabled": false,
  "remote_mutations_enabled": false
}
```

These booleans are server safety switches. Disabled capabilities remain disabled
in the form and cannot be raised by a request. Set the corresponding
`ACCESSPILOT_ENABLE_*` value on the API server and restart it to expose the
per-run checkbox. The UI also uses safe built-in WCAG and scan-depth defaults
because those options are request fields rather than public configuration
fields.

### `POST /api/runs`

```json
{
  "target": "https://example.test",
  "branch_name": "accesspilot/a11y-repairs",
  "wcag_level": "AA",
  "routes": ["/", "/account"],
  "scan_depth": 2,
  "llm_provider": "gemini",
  "authentication_instructions": "Use the test-account environment variables",
  "app_url": "https://staging.example.test",
  "allow_local_execution": false,
  "allow_dependency_install": false,
  "execute_pull_request": false
}
```

For a repository, provide `app_url` to scan an already-running deployment, or
enable the two independently server-gated local runtime flags. Dependency
installation is exposed by the UI only when local execution is also selected.
Remote publication requires both server capability
(`remote_mutations_enabled=true`) and the per-run opt-in
(`execute_pull_request=true`). The checkbox is deliberately unchecked by
default.

The create endpoint returns an accepted-run reference:

```json
{
  "run_id": "run-123",
  "status": "queued",
  "poll_url": "/api/runs/run-123"
}
```

### `GET /api/runs/{run_id}`

The polling endpoint returns the workflow state and event history:

```json
{
  "state": {
    "run_id": "run-123",
    "request": {
      "target": "https://example.test",
      "execute_pull_request": false
    },
    "status": "waiting_for_approval",
    "stage": "human_approval",
    "issues": [
      {
        "id": "color-contrast:/#submit",
        "title": "Button text has insufficient contrast",
        "severity": "serious",
        "wcag_references": ["1.4.3"],
        "route": "/",
        "selector": "#submit",
        "explanation": "...",
        "suggested_fix": "...",
        "confidence": 0.94,
        "status": "fixed"
      }
    ],
    "repair_plan": {
      "actions": [
        {
          "id": "repair-1",
          "issue_ids": ["label:email"],
          "file_path": "src/components/Signup.tsx",
          "strategy": "Associate the existing text with the input using a label.",
          "risk": "low"
        }
      ]
    },
    "changes": [
      {
        "path": "src/components/Signup.tsx",
        "additions": 4,
        "deletions": 2,
        "summary": "Restore semantic form labeling",
        "diff": "--- a/src/components/Signup.tsx\n+++ b/src/components/Signup.tsx\n..."
      }
    ],
    "verification": {
      "baseline_issue_count": 8,
      "current_issue_count": 2,
      "fixed_issue_count": 6,
      "remaining_issue_count": 2,
      "diff_sha256": "..."
    },
    "preview": {
      "original_url": "http://127.0.0.1:4100",
      "fixed_url": "http://127.0.0.1:4200"
    },
    "pr_draft": {
      "title": "Improve signup form accessibility",
      "body": "...",
      "commit_message": "fix(a11y): label signup controls",
      "branch": "accesspilot/a11y-repairs"
    }
  },
  "events": [
    {
      "sequence": 1,
      "run_id": "run-123",
      "stage": "intake",
      "status": "running",
      "message": "Validated request."
    }
  ]
}
```

Issue `status` is one of `open`, `planned`, `fixed`, `unresolved`, or
`regression`. If no top-level diff summary is provided, the UI concatenates the
per-file `changes[].diff` values for review.

Polling pauses at `waiting_for_approval` and terminal states. The manual refresh
button remains available.

### `POST /api/runs/{run_id}/approval`

```json
{
  "decision": "revise",
  "reviewer_note": "Keep the existing component API and add a focused test.",
  "expected_diff_sha256": "..."
}
```

`decision` is `approve`, `revise`, or `reject`. Every decision is bound to the
displayed `verification.diff_sha256`; the API returns `409` if the fingerprint
does not match the current working tree. The response is another
`{ "state": {...}, "events": [...] }` snapshot. Approving a dry run records the
decision without pushing a branch or creating a pull request. A revision returns
the run to the repair loop and requires a fresh verified diff before any later
approval.
