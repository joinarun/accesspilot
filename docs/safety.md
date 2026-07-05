# Safety model

## Source isolation

Local targets must be directories beneath a root listed in
`ACCESSPILOT_ALLOWED_SOURCE_ROOTS` (the project root by default); filesystem and
home roots are refused. Local Git repositories are cloned with no hard links.
Non-Git folders are copied with VCS data, environments, dependencies, generated
output, `.env` files, private keys, and runtime artifacts excluded. GitHub URLs
must identify one HTTPS `github.com/owner/repository` without credentials and
are normalized before a shallow clone.

## Server and network policy

Local app execution, dependency installation, and remote mutations are disabled
unless the operator enables `ACCESSPILOT_ENABLE_LOCAL_EXECUTION`,
`ACCESSPILOT_ENABLE_DEPENDENCY_INSTALL`, or
`ACCESSPILOT_ENABLE_REMOTE_MUTATIONS`, respectively. A run must also set the
matching request flag. Requests cannot override a false server switch; both the
REST boundary and graph intake enforce the intersection.

The API accepts loopback clients only by default. Non-loopback clients require
`ACCESSPILOT_ALLOW_REMOTE_CLIENTS=true`; this is independent of CORS settings.
External website and `app_url` targets are HTTP(S)-only, reject embedded or
query-string credentials, and must resolve exclusively to global addresses.
`localhost`, private, loopback, and link-local targets require the explicit
`ACCESSPILOT_ALLOW_PRIVATE_NETWORK_TARGETS=true` opt-in. Configured, discovered,
and crawled routes remain same-origin. Route discovery does not inherit ambient
proxy settings and does not follow redirects. Network link crawling runs only
for the bundled demo or an application AccessPilot actually started on
loopback. External sites get `/` by default and require explicit input for
additional routes.

The standalone scanner accepts explicit HTTP(S) and `file:` URLs. For HTTP(S),
it rejects private, loopback, link-local, and other non-public destinations on
the initial URL, every browser request, and final URL unless the invocation uses
`--allow-private-network`. That override is for explicitly trusted local scans
and permits private access only to host/port endpoints explicitly supplied to
that invocation. Each page gets only its own endpoint allowance, so private
cross-origin subresources remain blocked even when the destination is another
supplied URL. The standalone CLI does not apply the workflow's source-root or
same-origin crawl policy.

Chromium is launched with QUIC disabled and non-proxied WebRTC UDP disabled;
the scan context also removes page-level `RTCPeerConnection` constructors before
site scripts run. Production deployments should still enforce the same private,
link-local, metadata, and UDP denial at the container/network boundary because
browser flags and interception are defense in depth.

## Process isolation

Commands use `asyncio.create_subprocess_exec`, never a shell. The executable
allow-list excludes shells, privilege tools, destructive utilities, and inline
interpreter evaluation. Install subcommands require the dependency-install
gate. New process groups are terminated on timeout or shutdown. The child
environment includes basic OS variables and strips
key/token/password/cookie/credential names. A managed local application is
stopped before the workflow waits for human review, and all managed processes
are stopped on graceful API shutdown.

This reduces risk but is not an OS sandbox. Production must place untrusted builds in disposable containers or microVMs with CPU, memory, filesystem, syscall, and network policies.

## Patch policy

Only regular, non-symlink files inside the run root can be edited. VCS metadata,
dependency folders, generated output, virtual environments, credentials,
secret-like filenames, oversized files, and paths escaping the root are
rejected. Repository guidance is bounded untrusted context and cannot relax
this policy. Agent unified diffs must be tied to plan-owned paths and issue IDs,
target one existing file, avoid create/delete/rename/copy, binary, and submodule
operations, and pass `git apply --check`. Current automatic rules are
deliberately narrow and trace every file change back to issue IDs.

## Remote mutations

Remote operations are impossible until all of these are true:

1. `ACCESSPILOT_ENABLE_REMOTE_MUTATIONS=true` and the run set
   `execute_pull_request=true`;
2. deterministic verification exists and passed;
3. a PR draft exists;
4. a human response resolves the exact ADK interrupt with decision `approve`;
5. the changed-file content fingerprint matches;
6. the verification fingerprint matches;
7. the PR-metadata publication fingerprint matches;
8. the reviewed repository origin and approved HEAD still match;
9. live working files still match their reviewed after-hashes and the approved
   HEAD contains their reviewed before-hashes;
10. no staged, modified, or untracked path exists outside the approved set.

AccessPilot stages only the approved paths, then rechecks the exact index path
set and staged blob hashes. The generated commit must have the approved HEAD as
its parent and exactly the reviewed paths/content. Git hooks are disabled with
`core.hooksPath=/dev/null` plus `--no-verify`; push sends the exact commit SHA to
the reviewed non-force branch.

Local branch, index, and commit commands receive no GitHub token. Only push and
`gh` network commands receive server-provided `GH_TOKEN`/`GITHUB_TOKEN`, inside
an isolated temporary home with normal credential helpers cleared. Existing
user Git/`gh` credentials on disk are not reused. A matching branch pull request
is reused when found; otherwise `gh pr create` runs. Publication is not
transactional: failure after the local commit or push can leave a local commit
or remote branch for manual cleanup. Rejection and any mismatch stop safely.
