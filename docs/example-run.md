# Bundled example run

The fixture in `examples/vulnerable-app/` intentionally contains missing language and image text, skipped headings, a generic clickable control, poor contrast, suppressed focus, missing landmarks, and visual-only error messaging.

## Reproduce

```bash
agents-cli install
(cd scanner && npm install && npx playwright install chromium)
(cd frontend && npm install && npm run build)

ACCESSPILOT_DETERMINISTIC_SPECIALISTS=true \
ACCESSPILOT_PUBLIC_BASE_URL=http://127.0.0.1:8000 \
uv run uvicorn app.fast_api_app:app --host 127.0.0.1 --port 8000
```

In the UI, choose **Sample app with accessibility issue** and run the default AA scan.
AA filtering is cumulative across the supported WCAG 2.0, 2.1, and 2.2 axe
tags. The exact baseline count is intentionally not a release invariant: it can
change with axe rules, focused source checks, and fixture edits.

| Result | Count |
|---|---:|
| End-to-end AA baseline | non-empty, exact value varies |
| End-to-end verification issues | 0 |
| Changed files | 2 (`index.html`, `styles.css`) |
| New regressions | 0 |
| Remote actions after rejection | 0 |

The current integration assertion is a non-empty AA baseline, zero verified
findings for the bundled fixture, a passed verification report, the two expected
changed files, a resumable review gate, and no remote action on rejection. It
also checks that a revision cannot exceed the configured repair-round limit and
that a per-run remote request cannot override disabled server policy.

Run the same assertion:

```bash
uv run pytest -q tests/integration/test_server_e2e.py
```

The checked-in local scan-only trace currently reports 7 original findings for
the vulnerable page and 0 for the AccessPilot UI. Its narrow contract grading
currently reports:

```text
scan_only_vulnerable_site  1.0  PASS
scan_only_accesspilot_ui   1.0  PASS
2/2 cases passed
```

These one-turn scan-only cases check final-report/no-false-publication behavior;
they do not replace the end-to-end repair, verification, approval, or
publication tests. Start the local API/demo on `127.0.0.1:8000` and install
scanner dependencies before regenerating them. `generate_local_eval.py` enables
private targets only inside its short-lived local evaluator process.
