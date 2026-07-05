# Deliberately vulnerable web app

This small static site is an AccessPilot fixture. It is intentionally
inaccessible; do not treat it as a production example and do not repair it in
place when testing the repair workflow.

Serve it from this directory with any static file server, for example:

```sh
uv run python -m http.server 4173
```

Then scan the trusted loopback fixture explicitly:

```sh
node ../../scanner/src/scan.mjs --allow-private-network \
  http://127.0.0.1:4173
```

## Intentional issue inventory

- The document has no `lang` attribute.
- The hero image has no text alternative.
- The email input has no programmatic label.
- Heading levels skip from `h1` to `h3`, then use `h4` headings.
- “Start a project” is a clickable `div` with no keyboard semantics.
- Muted body copy has insufficient foreground/background contrast.
- Global CSS suppresses visible focus outlines.
- Form errors are inserted visually without `aria-live`, `role="alert"`, an
  input description, focus management, or invalid-state metadata.
- Toast status messages are also visual-only and disappear automatically.

The fixture mixes issues that axe can detect statically with interaction and
usability problems that require keyboard or assistive-technology verification.
