# Workflow graph

The root object is `Workflow(name="accesspilot_workflow")`, wrapped by `App(name="app", resumability_config=...)`.

## Main path

1. `IntakeNode` validates the target, routes, branch, WCAG level, provider, and
   execution flags against server policy. Per-run flags cannot enable a
   capability whose `ACCESSPILOT_ENABLE_*` switch is false.
2. `RepositoryLoaderNode` validates a URL or creates an isolated clone/copy.
3. `FrameworkDetectorNode` reads manifests without executing them.
4. `LocalAppRunnerNode` uses a supplied URL, bundled demo, or explicitly authorized allow-listed command.
5. `RouteDiscoveryNode` combines configured routes, bounded source conventions,
   and, for the bundled demo or an AccessPilot-managed loopback app, a
   same-origin breadth-first link crawl. An external site gets `/` by default;
   additional external routes require explicit input. `scan_depth=0` disables
   crawling; positive values (maximum 5) bound link depth, and the server route
   cap is 25 by default. Explicit and discovered URLs must preserve scheme,
   host, and effective port.
6. `AccessibilityScanNode` runs the external scanner with cumulative WCAG
   A/AA/AAA axe tags (AA includes A; AAA includes A and AA), then appends focused
   source-level keyboard/focus/error checks filtered to the same cumulative
   level. By default the scanner rejects private, loopback, and link-local
   destinations on the initial URL, every browser request, and final URL. The
   workflow opts out only for its bundled sample, an app it actually started on
   loopback, or a server-wide private-target opt-in. The exception is confined
   to the currently scanned target endpoint; private cross-origin subresources
   remain blocked.
7. `IssueNormalizationNode` creates stable issue IDs from rule, canonical route, and selector.
8. The source mapper and investigator rank bounded evidence. A path is selected
   for automatic ownership only when the best confidence is at least 0.6;
   weaker candidates remain unselected.
9. Auditor, investigator, planner, repairer, verifier, and PR writer agents add
   typed judgment at their respective stages. They are tool-free and receive
   minimal role-specific DTOs. Bounded repository contribution guidance is
   treated as untrusted context, never authority.
10. The planner may group related issues and order dependencies. Reconciliation
    starts from deterministic issue/path ownership, ignores unknown issue IDs,
    and may authorize an additional existing editable source, routing, or test
    file named by the group. Planner-added files are non-automatic. The repairer
    may propose plan-bound unified diffs; proposals with unowned issues or
    paths, unsafe operations, or a failing `git apply --check` are ignored.
11. `PatchValidationNode` applies validated proposals plus conservative
    HTML/CSS/JSX/TSX/Vue/Svelte transformations to workspace-confined regular
    files and records before/after hashes and review diffs. Deterministic
    transformations run only for eligible automatic actions; a planner-added
    file changes only through a validated repairer diff.
12. `VerificationScanNode` repeats scans and project checks.
13. `RegressionCheckNode` compares stable fingerprints. It may route back to
    planning only while state changes and the configured round cap remains.
14. Before `HumanApprovalNode` yields ADK `RequestInput`, AccessPilot stops the
    managed local app process. Its interrupt ID includes run ID and diff hash.
15. `PullRequestNode` requires passing verification and revalidates live files,
    index content, HEAD, origin, verification, and publication metadata before
    optional commit/push/PR creation.
16. `FinalReportNode` emits UI content and a structured report.

## Fallback routes

- URL without source: scan and report; no claimed repair.
- Source without a runnable URL: source-only final report.
- Unsupported or unsafe finding: unresolved issue in the review.
- No changed patch: stop instead of retrying.
- Same issue or diff fingerprint: stop the repair loop.
- Rejected approval: final report with no remote action.
- Revision requested: invalidate approval-derived state and consume only a
  remaining bounded repair round; stop if the round limit is already reached.

Transient clone, app-start, baseline scan, and verification scan nodes have two attempts with bounded backoff. Policy or validation errors are not silently retried by service code.
