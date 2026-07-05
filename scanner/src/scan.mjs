#!/usr/bin/env node

import { createHash } from "node:crypto";
import { lookup } from "node:dns/promises";
import { mkdir } from "node:fs/promises";
import { createServer, request as httpRequest } from "node:http";
import { isIP } from "node:net";
import { connect as netConnect } from "node:net";
import path from "node:path";
import process from "node:process";

const SCHEMA_VERSION = "1.0.0";
const TOOL_NAME = "accesspilot-axe-scanner";
const TOOL_VERSION = "0.1.0";
const DEFAULT_TIMEOUT_MS = 30_000;
const MAX_TIMEOUT_MS = 600_000;
const DEFAULT_WAIT_UNTIL = "domcontentloaded";
const DEFAULT_WCAG_LEVEL = "AA";
const WAIT_UNTIL_VALUES = new Set([
  "commit",
  "domcontentloaded",
  "load",
  "networkidle",
]);
const WCAG_TAGS_BY_LEVEL = Object.freeze({
  A: Object.freeze(["wcag2a", "wcag21a", "wcag22a"]),
  AA: Object.freeze([
    "wcag2a",
    "wcag21a",
    "wcag22a",
    "wcag2aa",
    "wcag21aa",
    "wcag22aa",
  ]),
  AAA: Object.freeze([
    "wcag2a",
    "wcag21a",
    "wcag22a",
    "wcag2aa",
    "wcag21aa",
    "wcag22aa",
    "wcag2aaa",
    "wcag21aaa",
    "wcag22aaa",
  ]),
});
const SUPPORTED_PROTOCOLS = new Set(["http:", "https:", "file:"]);
const IMPACT_ORDER = new Map([
  ["critical", 0],
  ["serious", 1],
  ["moderate", 2],
  ["minor", 3],
  [null, 4],
]);

class CliError extends Error {
  constructor(message) {
    super(message);
    this.name = "CliError";
  }
}

function usage() {
  return `AccessPilot deterministic accessibility scanner

Usage:
  node src/scan.mjs [options] <url> [url ...]
  node src/scan.mjs [options] --url <url> [--url <url> ...]

Options:
  -u, --url <url>              Add a URL to scan (repeatable)
      --wcag-level <level>     A, AA, or AAA; includes lower levels (default: AA)
      --allow-private-network  Permit loopback/private targets for trusted local scans
      --screenshot-dir <path>  Capture one full-page PNG per successful scan
      --timeout <milliseconds> Navigation and axe timeout (default: 30000)
      --wait-until <state>     commit, domcontentloaded, load, or networkidle
      --pretty                 Pretty-print JSON output
  -h, --help                   Show this help

The scanner writes only its JSON report to stdout. Axe violations do not change
the exit code; URL or browser failures set exit code 1, and CLI errors set 2.`;
}

function requireValue(args, index, option) {
  const value = args[index + 1];
  if (value === undefined || value.startsWith("-")) {
    throw new CliError(`${option} requires a value.`);
  }
  return value;
}

function parsePositiveInteger(value, option) {
  if (!/^\d+$/.test(value)) {
    throw new CliError(`${option} must be a positive integer.`);
  }

  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed <= 0 || parsed > MAX_TIMEOUT_MS) {
    throw new CliError(
      `${option} must be between 1 and ${MAX_TIMEOUT_MS} milliseconds.`,
    );
  }
  return parsed;
}

function parseArgs(args) {
  const options = {
    pretty: false,
    screenshotDir: null,
    allowPrivateNetwork: false,
    timeoutMs: DEFAULT_TIMEOUT_MS,
    urls: [],
    waitUntil: DEFAULT_WAIT_UNTIL,
    wcagLevel: DEFAULT_WCAG_LEVEL,
  };

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];

    if (arg === "--") {
      options.urls.push(...args.slice(index + 1));
      break;
    }
    if (arg === "-h" || arg === "--help") {
      return { help: true, options };
    }
    if (arg === "-u" || arg === "--url") {
      options.urls.push(requireValue(args, index, arg));
      index += 1;
      continue;
    }
    if (arg === "--screenshot-dir") {
      options.screenshotDir = requireValue(args, index, arg);
      index += 1;
      continue;
    }
    if (arg === "--wcag-level") {
      const wcagLevel = requireValue(args, index, arg).toUpperCase();
      if (!(wcagLevel in WCAG_TAGS_BY_LEVEL)) {
        throw new CliError(`${arg} must be one of: A, AA, AAA.`);
      }
      options.wcagLevel = wcagLevel;
      index += 1;
      continue;
    }
    if (arg === "--timeout") {
      options.timeoutMs = parsePositiveInteger(
        requireValue(args, index, arg),
        arg,
      );
      index += 1;
      continue;
    }
    if (arg === "--wait-until") {
      const waitUntil = requireValue(args, index, arg);
      if (!WAIT_UNTIL_VALUES.has(waitUntil)) {
        throw new CliError(
          `${arg} must be one of: ${[...WAIT_UNTIL_VALUES].join(", ")}.`,
        );
      }
      options.waitUntil = waitUntil;
      index += 1;
      continue;
    }
    if (arg === "--pretty") {
      options.pretty = true;
      continue;
    }
    if (arg === "--allow-private-network") {
      options.allowPrivateNetwork = true;
      continue;
    }
    if (arg.startsWith("-")) {
      throw new CliError(`Unknown option: ${arg}`);
    }
    options.urls.push(arg);
  }

  if (options.urls.length === 0) {
    throw new CliError("At least one URL is required.");
  }

  return { help: false, options };
}

function emptySummary() {
  return {
    inapplicableCount: 0,
    incompleteCount: 0,
    nodeCount: 0,
    passCount: 0,
    violationCount: 0,
  };
}

function newResult(inputUrl) {
  return {
    inputUrl,
    url: null,
    status: "error",
    httpStatus: null,
    title: null,
    screenshotPath: null,
    summary: emptySummary(),
    violations: [],
    incomplete: [],
    warnings: [],
    error: null,
  };
}

function normalizeTarget(target) {
  if (!Array.isArray(target)) {
    return [String(target)];
  }
  return target.map((part) =>
    Array.isArray(part) ? normalizeTarget(part) : String(part),
  );
}

function normalizeNode(node) {
  return {
    target: normalizeTarget(node.target ?? []),
    html: node.html ?? "",
    failureSummary: node.failureSummary ?? null,
  };
}

function compareNodes(left, right) {
  return JSON.stringify(left.target).localeCompare(JSON.stringify(right.target));
}

function normalizeRule(rule) {
  const tags = [...(rule.tags ?? [])].map(String).sort();
  const wcag = tags.filter((tag) => tag.startsWith("wcag"));

  return {
    id: String(rule.id ?? ""),
    impact: rule.impact ?? null,
    description: String(rule.description ?? ""),
    help: String(rule.help ?? ""),
    helpUrl: String(rule.helpUrl ?? ""),
    tags,
    wcag,
    nodes: [...(rule.nodes ?? [])].map(normalizeNode).sort(compareNodes),
  };
}

function compareRules(left, right) {
  const leftImpact = IMPACT_ORDER.get(left.impact) ?? IMPACT_ORDER.get(null);
  const rightImpact = IMPACT_ORDER.get(right.impact) ?? IMPACT_ORDER.get(null);
  return leftImpact - rightImpact || left.id.localeCompare(right.id);
}

function normalizeRules(rules) {
  return [...rules].map(normalizeRule).sort(compareRules);
}

function countNodes(rules) {
  return rules.reduce((total, rule) => total + rule.nodes.length, 0);
}

function slugify(value) {
  return (
    value
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 48) || "page"
  );
}

function screenshotFilename(url, index) {
  const parsed = new URL(url);
  const location = `${parsed.hostname || "local-file"}${parsed.pathname}`;
  const digest = createHash("sha256").update(url).digest("hex").slice(0, 8);
  return `${String(index + 1).padStart(3, "0")}-${slugify(location)}-${digest}.png`;
}

function normalizeError(error, fallbackCode = "SCAN_FAILED") {
  const message = error instanceof Error ? error.message : String(error);
  const name = error instanceof Error ? error.name : "Error";
  const isTimeout = name === "TimeoutError";

  return {
    code: isTimeout ? "TIMEOUT" : fallbackCode,
    name,
    message,
  };
}

function validateUrl(inputUrl) {
  let parsed;
  try {
    parsed = new URL(inputUrl);
  } catch {
    throw new CliError(
      `Invalid URL "${inputUrl}". Include a supported scheme such as http:// or https://.`,
    );
  }

  if (!SUPPORTED_PROTOCOLS.has(parsed.protocol)) {
    throw new CliError(
      `Unsupported URL protocol "${parsed.protocol}" for "${inputUrl}".`,
    );
  }
  return parsed.href;
}

function isPrivateAddress(address) {
  const normalized = address.toLowerCase().split("%", 1)[0];
  if (isIP(normalized) === 4) {
    const [a, b] = normalized.split(".").map(Number);
    return (
      a === 0 ||
      a === 10 ||
      a === 127 ||
      (a === 100 && b >= 64 && b <= 127) ||
      (a === 169 && b === 254) ||
      (a === 172 && b >= 16 && b <= 31) ||
      (a === 192 && [0, 168].includes(b)) ||
      (a === 198 && [18, 19, 51].includes(b)) ||
      (a === 203 && b === 0) ||
      a >= 224
    );
  }
  if (isIP(normalized) === 6) {
    if (normalized.startsWith("::ffff:")) {
      return isPrivateAddress(normalized.slice("::ffff:".length));
    }
    return (
      normalized === "::" ||
      normalized === "::1" ||
      normalized.startsWith("fc") ||
      normalized.startsWith("fd") ||
      /^fe[89ab]/.test(normalized) ||
      normalized.startsWith("ff") ||
      normalized.startsWith("2001:db8:")
    );
  }
  return true;
}

function endpointKey(hostname, port) {
  const normalized = hostname
    .replace(/^\[|\]$/g, "")
    .replace(/\.$/, "")
    .toLowerCase();
  return `${normalized}:${port}`;
}

function parsedEndpointKey(parsed) {
  const defaultPort = parsed.protocol === "https:" ? 443 : 80;
  return endpointKey(parsed.hostname, Number(parsed.port || defaultPort));
}

function privateEndpointAllowlist(allowPrivateNetwork, urls) {
  if (!allowPrivateNetwork) return new Set();
  const endpoints = new Set();
  for (const value of urls) {
    try {
      const parsed = new URL(value);
      if (["http:", "https:"].includes(parsed.protocol)) {
        endpoints.add(parsedEndpointKey(parsed));
      }
    } catch {
      // Invalid inputs are reported per URL by scanUrl.
    }
  }
  return endpoints;
}

async function enforceNetworkPolicy(value, allowedPrivateEndpoints) {
  const parsed = new URL(value);
  if (!["http:", "https:"].includes(parsed.protocol)) return;
  await resolveApprovedAddress(
    parsed.hostname,
    allowedPrivateEndpoints.has(parsedEndpointKey(parsed)),
  );
}

async function resolveApprovedAddress(rawHostname, allowPrivateAddress) {
  const hostname = rawHostname.replace(/^\[|\]$/g, "").toLowerCase();
  if (hostname === "localhost" || hostname.endsWith(".localhost")) {
    if (!allowPrivateAddress) {
      throw new CliError("Private-network targets require --allow-private-network.");
    }
  }
  const addresses = isIP(hostname)
    ? [{ address: hostname }]
    : await lookup(hostname, { all: true, verbatim: true });
  if (
    addresses.length === 0 ||
    (!allowPrivateAddress && addresses.some(({ address }) => isPrivateAddress(address)))
  ) {
    throw new CliError("Private, loopback, and link-local targets are blocked.");
  }
  return addresses[0].address;
}

async function startFilteredProxy() {
  let allowedPrivateEndpoints = new Set();
  const clientSockets = new Set();
  const server = createServer((request, response) => {
    void (async () => {
      try {
        const target = new URL(request.url);
        if (target.protocol !== "http:") {
          throw new CliError("Only HTTP proxy requests are supported.");
        }
        const address = await resolveApprovedAddress(
          target.hostname,
          allowedPrivateEndpoints.has(parsedEndpointKey(target)),
        );
        const headers = { ...request.headers, host: target.host };
        delete headers["proxy-connection"];
        const upstream = httpRequest(
          {
            host: address,
            port: target.port || 80,
            method: request.method,
            path: `${target.pathname}${target.search}`,
            headers,
          },
          (upstreamResponse) => {
            response.writeHead(
              upstreamResponse.statusCode ?? 502,
              upstreamResponse.headers,
            );
            upstreamResponse.pipe(response);
          },
        );
        upstream.on("error", () => {
          if (!response.headersSent) response.writeHead(502);
          response.end();
        });
        request.pipe(upstream);
      } catch (error) {
        response.writeHead(403, { "content-type": "text/plain" });
        response.end(error instanceof Error ? error.message : "Blocked");
      }
    })();
  });

  server.on("connect", (request, clientSocket, head) => {
    void (async () => {
      try {
        const separator = request.url.lastIndexOf(":");
        if (separator <= 0) throw new CliError("Invalid CONNECT target.");
        const hostname = request.url.slice(0, separator).replace(/^\[|\]$/g, "");
        const port = Number(request.url.slice(separator + 1));
        if (!Number.isInteger(port) || port < 1 || port > 65535) {
          throw new CliError("Invalid CONNECT port.");
        }
        const address = await resolveApprovedAddress(
          hostname,
          allowedPrivateEndpoints.has(endpointKey(hostname, port)),
        );
        const upstream = netConnect({ host: address, port });
        upstream.once("connect", () => {
          clientSocket.write("HTTP/1.1 200 Connection Established\r\n\r\n");
          if (head.length > 0) upstream.write(head);
          upstream.pipe(clientSocket);
          clientSocket.pipe(upstream);
        });
        upstream.once("error", () => clientSocket.destroy());
        clientSocket.once("error", () => upstream.destroy());
        clientSocket.once("close", () => upstream.destroy());
      } catch {
        clientSocket.write("HTTP/1.1 403 Forbidden\r\n\r\n");
        clientSocket.destroy();
      }
    })();
  });
  server.on("connection", (socket) => {
    clientSockets.add(socket);
    socket.once("close", () => clientSockets.delete(socket));
  });
  server.on("upgrade", (_request, socket) => socket.destroy());

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string") {
    server.close();
    throw new Error("Filtered proxy did not expose a TCP address.");
  }
  return {
    url: `http://127.0.0.1:${address.port}`,
    setAllowedPrivateEndpoints: (endpoints) => {
      allowedPrivateEndpoints = new Set(endpoints);
      for (const socket of clientSockets) socket.destroy();
    },
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

async function captureScreenshot(page, screenshotDir, inputUrl, index) {
  const absoluteDir = path.resolve(screenshotDir);
  await mkdir(absoluteDir, { recursive: true });
  const screenshotPath = path.join(
    absoluteDir,
    screenshotFilename(inputUrl, index),
  );
  await page.screenshot({ path: screenshotPath, fullPage: true });
  return screenshotPath;
}

async function withTimeout(promise, timeoutMs, message) {
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timer = setTimeout(() => {
          const error = new Error(message);
          error.name = "TimeoutError";
          reject(error);
        }, timeoutMs);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

async function loadScannerDependencies() {
  const [axeModule, playwrightModule] = await Promise.all([
    import("@axe-core/playwright"),
    import("playwright"),
  ]);
  const AxeBuilder = axeModule.default ?? axeModule.AxeBuilder;

  if (typeof AxeBuilder !== "function" || !playwrightModule.chromium) {
    throw new Error("Scanner dependencies did not expose the expected APIs.");
  }

  return { AxeBuilder, chromium: playwrightModule.chromium };
}

async function scanUrl(browser, AxeBuilder, inputUrl, index, options) {
  const result = newResult(inputUrl);
  let context;

  try {
    const normalizedUrl = validateUrl(inputUrl);
    await enforceNetworkPolicy(normalizedUrl, options.allowedPrivateEndpoints);
    context = await browser.newContext({
      colorScheme: "light",
      locale: "en-US",
      reducedMotion: "reduce",
      serviceWorkers: "block",
      viewport: { width: 1440, height: 900 },
    });
    await context.addInitScript(() => {
      for (const name of ["RTCPeerConnection", "webkitRTCPeerConnection"]) {
        Object.defineProperty(globalThis, name, {
          configurable: false,
          value: undefined,
          writable: false,
        });
      }
    });
    const page = await context.newPage();
    await page.route("**/*", async (route) => {
      try {
        await enforceNetworkPolicy(
          route.request().url(),
          options.allowedPrivateEndpoints,
        );
        await route.continue();
      } catch {
        await route.abort("blockedbyclient");
      }
    });
    page.setDefaultTimeout(options.timeoutMs);
    page.setDefaultNavigationTimeout(options.timeoutMs);

    const response = await page.goto(normalizedUrl, {
      timeout: options.timeoutMs,
      waitUntil: options.waitUntil,
    });

    result.url = page.url();
    await enforceNetworkPolicy(result.url, options.allowedPrivateEndpoints);
    if (new URL(result.url).origin !== new URL(normalizedUrl).origin) {
      throw new CliError("Cross-origin top-level redirects are not permitted.");
    }
    result.httpStatus = response?.status() ?? null;
    result.title = await page.title();

    const axeResults = await withTimeout(
      new AxeBuilder({ page })
        .withTags(WCAG_TAGS_BY_LEVEL[options.wcagLevel])
        .analyze(),
      options.timeoutMs,
      `axe analysis exceeded ${options.timeoutMs} milliseconds.`,
    );

    result.violations = normalizeRules(axeResults.violations ?? []);
    result.incomplete = normalizeRules(axeResults.incomplete ?? []);
    result.summary = {
      inapplicableCount: axeResults.inapplicable?.length ?? 0,
      incompleteCount: result.incomplete.length,
      nodeCount:
        countNodes(result.violations) + countNodes(result.incomplete),
      passCount: axeResults.passes?.length ?? 0,
      violationCount: result.violations.length,
    };

    if (options.screenshotDir) {
      try {
        result.screenshotPath = await captureScreenshot(
          page,
          options.screenshotDir,
          normalizedUrl,
          index,
        );
      } catch (error) {
        result.warnings.push({
          ...normalizeError(error, "SCREENSHOT_FAILED"),
          code: "SCREENSHOT_FAILED",
        });
      }
    }

    result.status =
      result.warnings.length > 0 ? "completed_with_warnings" : "completed";
  } catch (error) {
    result.error = normalizeError(
      error,
      error instanceof CliError ? "INVALID_URL" : "SCAN_FAILED",
    );
  } finally {
    await context?.close().catch(() => undefined);
  }

  return result;
}

function reportOptions(options) {
  return {
    timeoutMs: options.timeoutMs,
    waitUntil: options.waitUntil,
    wcagLevel: options.wcagLevel,
    allowPrivateNetwork: options.allowPrivateNetwork,
    screenshotDir: options.screenshotDir
      ? path.resolve(options.screenshotDir)
      : null,
  };
}

function buildReport(options, results, fatalError = null) {
  const completed = results.filter((result) =>
    result.status.startsWith("completed"),
  ).length;

  return {
    schemaVersion: SCHEMA_VERSION,
    tool: {
      name: TOOL_NAME,
      version: TOOL_VERSION,
      engine: "axe-core/playwright",
    },
    status: fatalError || completed !== results.length ? "error" : "completed",
    options: options ? reportOptions(options) : null,
    summary: {
      requested: results.length,
      completed,
      failed: results.length - completed,
      inapplicableCount: results.reduce(
        (total, result) => total + result.summary.inapplicableCount,
        0,
      ),
      violationCount: results.reduce(
        (total, result) => total + result.summary.violationCount,
        0,
      ),
      incompleteCount: results.reduce(
        (total, result) => total + result.summary.incompleteCount,
        0,
      ),
      nodeCount: results.reduce(
        (total, result) => total + result.summary.nodeCount,
        0,
      ),
      passCount: results.reduce(
        (total, result) => total + result.summary.passCount,
        0,
      ),
    },
    results,
    fatalError,
  };
}

function writeJson(value, pretty) {
  process.stdout.write(`${JSON.stringify(value, null, pretty ? 2 : 0)}\n`);
}

async function main() {
  let parsed;
  try {
    parsed = parseArgs(process.argv.slice(2));
  } catch (error) {
    writeJson(
      buildReport(null, [], normalizeError(error, "INVALID_ARGUMENT")),
      true,
    );
    process.exitCode = 2;
    return;
  }

  if (parsed.help) {
    process.stdout.write(`${usage()}\n`);
    return;
  }

  const { options } = parsed;
  let dependencies;
  try {
    dependencies = await loadScannerDependencies();
  } catch (error) {
    const dependencyError = normalizeError(error, "DEPENDENCY_LOAD_FAILED");
    const results = options.urls.map((inputUrl) => ({
      ...newResult(inputUrl),
      error: dependencyError,
    }));
    writeJson(buildReport(options, results, dependencyError), options.pretty);
    process.exitCode = 1;
    return;
  }

  let browser;
  let networkProxy;

  try {
    networkProxy = await startFilteredProxy();
    browser = await dependencies.chromium.launch({
      args: [
        "--disable-quic",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
      ],
      headless: true,
      proxy: { server: networkProxy.url },
    });
  } catch (error) {
    await networkProxy?.close().catch(() => undefined);
    const browserError = normalizeError(error, "BROWSER_LAUNCH_FAILED");
    const results = options.urls.map((inputUrl) => ({
      ...newResult(inputUrl),
      error: browserError,
    }));
    writeJson(buildReport(options, results, browserError), options.pretty);
    process.exitCode = 1;
    return;
  }

  try {
    const results = [];
    for (const [index, inputUrl] of options.urls.entries()) {
      const allowedPrivateEndpoints = privateEndpointAllowlist(
        options.allowPrivateNetwork,
        [inputUrl],
      );
      networkProxy.setAllowedPrivateEndpoints(allowedPrivateEndpoints);
      results.push(
        await scanUrl(
          browser,
          dependencies.AxeBuilder,
          inputUrl,
          index,
          { ...options, allowedPrivateEndpoints },
        ),
      );
    }

    const report = buildReport(options, results);
    writeJson(report, options.pretty);
    if (report.summary.failed > 0) {
      process.exitCode = 1;
    }
  } finally {
    await browser.close().catch(() => undefined);
    await networkProxy.close().catch(() => undefined);
  }
}

await main().catch((error) => {
  writeJson(
    buildReport(null, [], normalizeError(error, "UNEXPECTED_ERROR")),
    true,
  );
  process.exitCode = 1;
});
