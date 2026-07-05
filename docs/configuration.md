# Configuration

All secrets are runtime environment variables. Credential values in
`.env.example` are deliberately blank so a copied example is never mistaken for
a configured provider.

## Provider-neutral settings

| Variable | Default | Purpose |
|---|---:|---|
| `ACCESSPILOT_LLM_PROVIDER` | `gemini` | `gemini` or `openai` |
| `ACCESSPILOT_WORKSPACE_ROOT` | `.accesspilot/workspaces` | Isolated source copies |
| `ACCESSPILOT_ARTIFACT_ROOT` | `.accesspilot/artifacts` | Screenshots and reports |
| `ACCESSPILOT_MAX_REPAIR_ROUNDS` | `3` | Server default; request is capped at 5 |
| `ACCESSPILOT_MAX_ROUTES` | `25` | Maximum explicit/discovered/crawled routes per run |
| `ACCESSPILOT_COMMAND_TIMEOUT_SECONDS` | `180` | Bounded project command time |
| `ACCESSPILOT_PUBLIC_BASE_URL` | `http://127.0.0.1:8000` | Demo/preview URL base |
| `ACCESSPILOT_DETERMINISTIC_SPECIALISTS` | false | Force typed local specialists for the scaffold/native ADK root graph |
| `ACCESSPILOT_ENABLE_LOCAL_EXECUTION` | false | Permit runs that explicitly request a local app start |
| `ACCESSPILOT_ENABLE_DEPENDENCY_INSTALL` | false | Permit runs that explicitly request dependency installation |
| `ACCESSPILOT_ENABLE_REMOTE_MUTATIONS` | false | Permit runs that explicitly request push/PR execution after approval |
| `ACCESSPILOT_ALLOW_REMOTE_CLIENTS` | false | Accept non-loopback API clients |
| `ACCESSPILOT_ALLOW_PRIVATE_NETWORK_TARGETS` | false | Permit private/loopback targets and browser requests for an explicitly trusted deployment |
| `ACCESSPILOT_ALLOWED_SOURCE_ROOTS` | project root | Platform-path-separated roots accepted as local source targets |

The three `ENABLE_*` settings are server ceilings, not defaults that a request
can elevate. Local execution requires both
`ACCESSPILOT_ENABLE_LOCAL_EXECUTION=true` and
`allow_local_execution=true`; dependency installation requires both its server
switch and `allow_dependency_install=true`; publication requires both its
server switch and `execute_pull_request=true`. The REST API returns `403` for a
request that exceeds server policy, and graph intake repeats the checks.
The web UI reads these capabilities from `/api/config`: its local-execution and
dependency-install checkboxes stay disabled unless the corresponding server
ceiling is enabled. Repository runs can instead provide `app_url` for an
already-running public or operator-approved deployment.

UI/API runs independently select the typed deterministic fallback whenever the
provider chosen by the request has no real runtime credentials. If valid
credentials are configured, those runs use the selected provider;
`ACCESSPILOT_DETERMINISTIC_SPECIALISTS` controls the scaffold/native root graph
used by `agents-cli` and direct ADK surfaces.

An empty `ACCESSPILOT_ALLOWED_SOURCE_ROOTS` resolves to the AccessPilot project
root. Separate additional roots with `:` on POSIX or `;` on Windows. Source is
always cloned or copied into a run workspace before inspection or editing.

## Client and target network policy

Except for health endpoints, the API accepts loopback clients only when
`ACCESSPILOT_ALLOW_REMOTE_CLIENTS=false`. This is an application policy separate
from CORS/`ALLOW_ORIGINS`.

Website and `app_url` targets must be HTTP(S), must not embed credentials, and
must resolve only to globally routable addresses by default. `localhost`,
private, loopback, and link-local targets require
`ACCESSPILOT_ALLOW_PRIVATE_NETWORK_TARGETS=true`. That opt-in relaxes the
address-class check; URL syntax and credential checks remain in force. The
bundled demo bypasses this external-target path. The Playwright scanner also
checks every browser request and the final navigation URL, not only the initial
page. The Python workflow passes the scanner's private-network override only for
the bundled sample, an app it actually started on loopback, or this server-wide
opt-in. The scanner confines that exception to the currently scanned target
host/port. Private cross-origin subresources remain blocked, including other
URLs supplied in the same invocation. GitHub source URLs are handled separately
and normalized to an HTTPS
`github.com/owner/repository.git` URL.

Configured, source-discovered, and crawled routes must remain on the base
scheme, hostname, and effective port. `scan_depth` ranges from 0 to 5: zero
disables link crawling, while positive values bound same-origin breadth-first
crawling for the bundled demo and AccessPilot-managed loopback apps. External
websites do not use the Python crawler: they get the default `/` route, while
additional routes require explicit input. The server still scans explicit,
source-discovered, and default routes, up to
`ACCESSPILOT_MAX_ROUTES` routes (25 by default).

## WCAG level

Requests accept `wcag_level=A|AA|AAA` (default `AA`). Axe filtering is
cumulative across WCAG 2.0, 2.1, and 2.2 tags: AA includes A rules, and AAA
includes A and AA rules. Focused deterministic source checks follow the same
cumulative selection: Level A includes the A checks, while AA and AAA also
include the focused visible-focus check.

## Gemini

Use `GEMINI_API_KEY` or `GOOGLE_API_KEY` for AI Studio, or configure
`GOOGLE_GENAI_USE_VERTEXAI=true`, `GOOGLE_CLOUD_PROJECT`, and
`GOOGLE_CLOUD_LOCATION` for Vertex AI. The model defaults to
`gemini-2.5-pro` and can be changed with `ACCESSPILOT_GEMINI_MODEL`.

```dotenv
ACCESSPILOT_LLM_PROVIDER=gemini
GEMINI_API_KEY=
```

## OpenAI through LLMproxy

Set `LLMPROXY_BASE_URL` and `LLMPROXY_API_KEY`. The model defaults to `gpt-4.1` and can be changed with `ACCESSPILOT_OPENAI_MODEL`.

```dotenv
ACCESSPILOT_LLM_PROVIDER=openai
LLMPROXY_BASE_URL=https://your-enterprise-proxy.example/openai/v1
LLMPROXY_API_KEY=
```

The factory:

- adds the `openai/` LiteLLM prefix;
- rejects `openai.com` and subdomains;
- rejects URL credentials, query strings, fragments, and missing values;
- reads only the selected provider’s credentials;
- never includes a credential value in models, reprs, logs, or errors.

## Target authentication

`authentication_instructions` accepts human-readable process guidance only.
Bearer values, JWTs, and named key/token/secret/password/cookie assignments are
rejected. This prototype does not store credentials, inject browser secrets, or
automate arbitrary login flows. A future credential broker/browser-context
integration is required for authenticated scanning; do not put secrets in
instructions, repository URLs, route strings, or source files.
