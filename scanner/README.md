# AccessPilot deterministic scanner

This package renders one or more pages in headless Chromium and runs
`@axe-core/playwright` against each page. It emits a normalized JSON report to
standard output so the AccessPilot workflow can consume scanner results without
parsing human-oriented logs.

## Setup

From this directory:

```sh
npm install
npx playwright install chromium
```

The repository does not vendor `node_modules` or a browser binary.

When AccessPilot is installed from its Python wheel, the wheel includes this
scanner source and lockfile. Run `accesspilot-install-scanner` from that same
user-owned virtual environment to install the pinned npm packages and Chromium;
Linux image builders can add `--with-deps` for Playwright OS packages.

## Usage

Scan one or more positional URLs:

```sh
node ./src/scan.mjs --pretty \
  --wcag-level AA \
  https://example.com
```

Loopback and private-network scans are denied by default. Opt in only for a
trusted local target:

```sh
node ./src/scan.mjs --pretty \
  --wcag-level AA \
  --allow-private-network \
  http://127.0.0.1:4173
```

URLs can also be supplied with repeatable flags. Use the silent npm form when
redirecting stdout so npm's own banner does not contaminate the JSON:

```sh
npm run --silent scan -- \
  --url https://example.com \
  --url https://example.org \
  --wcag-level AA \
  --timeout 20000 \
  --screenshot-dir ./artifacts/screenshots \
  --pretty > ./artifacts/report.json
```

Accepted URL schemes are `http:`, `https:`, and `file:`. Scans run sequentially
in the requested order, each in a fresh browser context with a fixed viewport,
locale, color scheme, and reduced-motion preference.

This standalone CLI scans only the URLs supplied to it. For HTTP(S), it resolves
and rejects private, loopback, link-local, and other non-public destinations by
default. The check runs for the initial URL, every intercepted browser request,
and the final URL after navigation. `--allow-private-network` permits each page
to access only its own supplied host/port endpoint. Private cross-origin
subresources remain blocked, including other URLs named in the same invocation.
Use it only for explicitly trusted local pages.

The CLI does not enforce the AccessPilot workflow's allowed-source-root or
same-origin crawl policy. The Python workflow validates its target, performs a
bounded same-origin `scan_depth` crawl only for the bundled demo or an app it
actually started on loopback, and passes the resulting URLs here. An external
site always gets its default `/` page; additional external routes require
explicit input. The workflow adds `--allow-private-network` only for the bundled
sample, a managed loopback app, or a server whose operator enabled private
targets.

`--wcag-level` accepts `A`, `AA`, or `AAA` and defaults to `AA`. Levels are
cumulative: `AA` runs all Level A and AA rules, while `AAA` runs Level A, AA,
and AAA rules. The pinned axe rule set applies the corresponding WCAG 2.0, 2.1,
and 2.2 tags available for each selected conformance level.

## Output contract

The top-level object always contains:

- `schemaVersion` and `tool` identifiers.
- The effective `options`, including `wcagLevel` and
  `allowPrivateNetwork` (or `null` for a CLI argument error).
- Aggregate `summary` counts.
- One ordered entry in `results` for every requested URL.
- `fatalError`, which is `null` unless setup fails before individual scans.

Every result has the same fields, including `summary`, `violations`,
`incomplete`, `warnings`, and `error`. Rules are sorted by impact and rule ID;
nodes and tags are also sorted for stable downstream comparisons. Volatile
timestamps and scan durations are intentionally omitted.

Exit codes:

- `0`: every URL was scanned, even if accessibility violations were found.
- `1`: a browser launch, navigation, or page scan failed.
- `2`: command-line arguments were invalid.

Run `node ./src/scan.mjs --help` for the complete option list.
