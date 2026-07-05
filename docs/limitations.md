# Known limitations

- The prototype executes allowed commands on the host. Server switches,
  shell-free argv validation, environment stripping, timeouts, and process-group
  cleanup are defense in depth, not a substitute for container or microVM
  isolation.
- In-memory runs and sessions do not survive a restart.
- Route discovery is bounded, same-origin, and convention-based; dynamic
  parameterized routes can require an explicit route list. The standalone
  scanner applies private/loopback/link-local checks to every HTTP(S) browser
  request by default, but it does not apply the workflow's source-root or
  same-origin crawl policy. Public cross-origin subresources remain possible,
  while an explicit private-network opt-in permits private access only to the
  page's own supplied target endpoint. These checks are defense in depth rather
  than a complete browser network sandbox. QUIC and non-proxied WebRTC UDP are
  disabled, but production still needs an egress-denying network boundary.
- Authentication instructions are non-secret guidance only. Arbitrary login
  automation, cookies/tokens, MFA, and credential brokerage are not implemented.
- The scanner covers rendered axe rules plus focused source checks. It cannot prove screen-reader quality, cognitive accessibility, content clarity, voice control, zoom/reflow usability, or mobile assistive-technology behavior.
- Automatic repair supports conservative HTML/CSS and narrow JSX/TSX/Vue/Svelte
  markup transformations. Framework detection and mapping cover more stacks,
  but Angular and other complex component, state, styling, build, routing, and
  framework-specific changes remain human work when no deterministic rule
  applies. Agent-proposed unified diffs are structurally/path validated, not a
  semantic proof that every hunk is correct.
- Alternative text inferred from filenames is medium-confidence and should be reviewed for meaning.
- Preview iframes may be blocked by target security headers; screenshot and open-in-new-tab fallbacks remain available.
- Managed dynamic app processes are stopped before human review. Screenshots and
  AccessPilot-served static previews remain, but the original dynamic URL may no
  longer be live. Some report-only failure paths can leave a child alive until
  the run is replaced or the API shuts down.
- A2A routes from the generated scaffold are not mounted for the root graph because ADK 2.3 `Workflow` is a `BaseNode`, while the scaffold’s A2A adapter accepts `BaseAgent`. Native ADK API routes remain available.
- `agents-cli` 1.0.0 eval generation and grading instantiate Vertex clients even
  for deterministic agents/metrics and therefore require Google ADC. The formal
  bundled eval must also opt into private targets because it scans loopback.
  Schema-valid local trace and metric scripts are included for credential-free
  development; local grading still requires the eval dependency that provides
  `vertexai.types`.
- Remote publication is strongly revalidated but not transactional: failures
  after commit or push can leave local or remote state for operator cleanup.
- The bundled image includes Node 20, Chromium, scanner dependencies, the built
  UI, and sample assets, but it remains a single-process prototype. It does not
  add durable sessions, a job queue, an authenticated edge, or per-run OS
  isolation.
