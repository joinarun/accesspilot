import type {
  AppConfig,
  ApprovalRequest,
  ChangedFile,
  Issue,
  IssueStatus,
  PreviewDetails,
  PullRequestDraft,
  RepairPlanItem,
  RunRequest,
  RunView,
  SelectOption,
  Severity,
  TestResult,
} from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

type JsonObject = Record<string, unknown>;

const fallbackProviders: SelectOption[] = [
  { value: "deterministic", label: "Deterministic (no LLM)" },
  { value: "gemini", label: "Gemini" },
  { value: "openai", label: "OpenAI" },
];

const fallbackWcagLevels: SelectOption[] = [
  { value: "A", label: "WCAG 2.2 Level A" },
  { value: "AA", label: "WCAG 2.2 Level AA" },
  { value: "AAA", label: "WCAG 2.2 Level AAA" },
];

export class ApiError extends Error {
  status: number;

  constructor(message: string, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function object(value: unknown): JsonObject {
  return isObject(value) ? value : {};
}

function pick(record: JsonObject, ...keys: string[]): unknown {
  for (const key of keys) {
    if (record[key] !== undefined && record[key] !== null) return record[key];
  }
  return undefined;
}

function stringValue(value: unknown, fallback = ""): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return fallback;
}

function numberValue(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return undefined;
}

function booleanValue(value: unknown, fallback = false): boolean {
  if (typeof value === "boolean") return value;
  if (typeof value === "string") {
    if (["true", "yes", "1"].includes(value.toLowerCase())) return true;
    if (["false", "no", "0"].includes(value.toLowerCase())) return false;
  }
  return fallback;
}

function stringList(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value
      .map((item) => stringValue(isObject(item) ? pick(item, "label", "name", "id") : item))
      .filter(Boolean);
  }
  if (typeof value === "string") {
    return value
      .split(/[,\n]/)
      .map((item) => item.trim())
      .filter(Boolean);
  }
  return [];
}

function normalizeOption(value: unknown): SelectOption | undefined {
  if (typeof value === "string") return { value, label: value };
  if (!isObject(value)) return undefined;

  const optionValue = stringValue(pick(value, "value", "id", "name", "provider"));
  if (!optionValue) return undefined;
  return {
    value: optionValue,
    label: stringValue(pick(value, "label", "display_name", "name"), optionValue),
    description: stringValue(pick(value, "description", "detail", "hint")) || undefined,
  };
}

function normalizeOptions(value: unknown, fallback: SelectOption[]): SelectOption[] {
  if (!Array.isArray(value)) return fallback;
  const options = value
    .map(normalizeOption)
    .filter((option): option is SelectOption => Boolean(option));
  return options.length > 0 ? options : fallback;
}

function normalizeSeverity(value: unknown): Severity {
  const severity = stringValue(value, "unknown").toLowerCase();
  if (["critical", "serious", "moderate", "minor"].includes(severity)) {
    return severity as Severity;
  }
  if (severity === "high" || severity === "error") return "serious";
  if (severity === "medium" || severity === "warning") return "moderate";
  if (severity === "low" || severity === "info") return "minor";
  return "unknown";
}

function normalizeConfidence(value: unknown): number | undefined {
  const confidence = numberValue(value);
  if (confidence === undefined) return undefined;
  return Math.max(0, Math.min(1, confidence > 1 ? confidence / 100 : confidence));
}

function normalizeIssue(value: unknown, index: number): Issue {
  const issue = object(value);
  const rule = stringValue(pick(issue, "rule_id", "rule", "code"));
  const selector = stringValue(pick(issue, "selector", "dom_selector", "target"), "Not provided");
  const route = stringValue(pick(issue, "route", "page", "url", "page_url"), "Unknown route");
  const issueId = stringValue(pick(issue, "id", "issue_id", "fingerprint"), `${rule || "issue"}-${index + 1}`);
  const rawStatus = stringValue(pick(issue, "status", "resolution_status")).toLowerCase();
  const fixed = booleanValue(pick(issue, "fixed", "resolved")) || ["fixed", "resolved"].includes(rawStatus);
  const status: IssueStatus = ["open", "planned", "fixed", "unresolved", "regression"].includes(rawStatus)
    ? (rawStatus as IssueStatus)
    : fixed
      ? "fixed"
      : "open";

  return {
    id: issueId,
    title: stringValue(pick(issue, "title", "name", "description"), rule || `Issue ${index + 1}`),
    severity: normalizeSeverity(pick(issue, "severity", "impact", "priority")),
    wcag: stringList(pick(issue, "wcag", "wcag_references", "wcag_reference", "tags")),
    route,
    selector,
    explanation: stringValue(
      pick(issue, "explanation", "plain_language_explanation", "description", "help"),
      "No explanation is available yet.",
    ),
    suggestedFix: stringValue(
      pick(issue, "suggested_fix", "suggested_fix_strategy", "fix", "recommendation"),
      "Repair strategy is still being investigated.",
    ),
    confidence: normalizeConfidence(pick(issue, "confidence", "confidence_score")),
    evidence: stringValue(pick(issue, "evidence", "dom_snippet", "snippet", "html")) || undefined,
    screenshotUrl: stringValue(pick(issue, "screenshot_url", "screenshot")) || undefined,
    status,
    fixed: status === "fixed",
  };
}

function normalizeRisk(value: unknown): RepairPlanItem["risk"] {
  const risk = stringValue(value, "unknown").toLowerCase();
  return ["low", "medium", "high"].includes(risk)
    ? (risk as RepairPlanItem["risk"])
    : "unknown";
}

function normalizePlanItem(value: unknown, index: number): RepairPlanItem {
  const item = object(value);
  return {
    id: stringValue(pick(item, "id", "plan_id"), `plan-${index + 1}`),
    title: stringValue(pick(item, "title", "name", "summary"), `Repair group ${index + 1}`),
    issueIds: stringList(pick(item, "issue_ids", "issues", "addresses")),
    strategy: stringValue(
      pick(item, "strategy", "fix_strategy", "description", "rationale"),
      "Repair details are being prepared.",
    ),
    files: stringList(pick(item, "files", "source_files", "affected_files", "file_path")),
    confidence: normalizeConfidence(pick(item, "confidence", "confidence_score")),
    risk: normalizeRisk(pick(item, "risk", "risk_level")),
    status: stringValue(pick(item, "status", "state"), "planned"),
  };
}

function normalizeChangedFile(value: unknown): ChangedFile | undefined {
  if (typeof value === "string") return { path: value };
  if (!isObject(value)) return undefined;
  const path = stringValue(pick(value, "path", "file", "filename"));
  if (!path) return undefined;
  return {
    path,
    additions: numberValue(pick(value, "additions", "lines_added")),
    deletions: numberValue(pick(value, "deletions", "lines_deleted")),
    summary: stringValue(pick(value, "summary", "description")) || undefined,
    diff: stringValue(pick(value, "diff", "patch", "unified_diff")) || undefined,
  };
}

function normalizeTestStatus(value: unknown): TestResult["status"] {
  const status = stringValue(value, "unknown").toLowerCase();
  if (["passed", "pass", "success", "ok"].includes(status)) return "passed";
  if (["failed", "fail", "error"].includes(status)) return "failed";
  if (["skipped", "skip", "not_run"].includes(status)) return "skipped";
  if (["running", "pending", "in_progress"].includes(status)) return "running";
  return "unknown";
}

function normalizeTest(value: unknown, index: number): TestResult {
  if (typeof value === "string") return { name: value, status: "unknown" };
  const item = object(value);
  const argv = stringList(pick(item, "argv"));
  const skippedReason = stringValue(pick(item, "skipped_reason"));
  const exitCode = numberValue(pick(item, "exit_code"));
  const inferredStatus = skippedReason
    ? "skipped"
    : exitCode === 0
      ? "passed"
      : exitCode !== undefined
        ? "failed"
        : "unknown";
  return {
    name:
      stringValue(pick(item, "name", "title", "check")) ||
      argv.join(" ") ||
      `Check ${index + 1}`,
    status:
      normalizeTestStatus(pick(item, "status", "result", "outcome")) === "unknown"
        ? inferredStatus
        : normalizeTestStatus(pick(item, "status", "result", "outcome")),
    command: stringValue(pick(item, "command", "cmd")) || argv.join(" ") || undefined,
    detail:
      skippedReason ||
      stringValue(pick(item, "detail", "message", "summary", "output", "stderr", "stdout")) ||
      undefined,
    duration: stringValue(pick(item, "duration", "elapsed")) || undefined,
  };
}

function normalizePreview(value: unknown, root: JsonObject): PreviewDetails {
  const preview = object(value);
  return {
    originalUrl:
      stringValue(pick(preview, "original_url", "before_url", "originalUrl")) ||
      stringValue(pick(root, "original_preview_url", "before_url")) ||
      undefined,
    fixedUrl:
      stringValue(pick(preview, "fixed_url", "after_url", "fixedUrl")) ||
      stringValue(pick(root, "fixed_preview_url", "after_url")) ||
      undefined,
    originalScreenshotUrl:
      stringValue(pick(preview, "original_screenshot_url", "original_screenshot", "before_screenshot")) || undefined,
    fixedScreenshotUrl:
      stringValue(pick(preview, "fixed_screenshot_url", "fixed_screenshot", "after_screenshot")) || undefined,
  };
}

function normalizePrDraft(value: unknown): PullRequestDraft | undefined {
  const draft = object(value);
  const title = stringValue(pick(draft, "title", "pr_title"));
  const body = stringValue(pick(draft, "body", "description", "pr_body"));
  const url = stringValue(pick(draft, "url", "html_url", "pr_url"));
  if (!title && !body && !url) return undefined;
  return {
    title: title || "Accessibility repairs",
    body,
    commitMessage: stringValue(pick(draft, "commit_message", "commit")) || undefined,
    branch: stringValue(pick(draft, "branch", "branch_name")) || undefined,
    url: url || undefined,
  };
}

function errorMessage(value: unknown): string | undefined {
  if (typeof value === "string" && value.trim()) return value;
  if (isObject(value)) {
    return stringValue(pick(value, "message", "detail", "error")) || undefined;
  }
  return undefined;
}

async function fetchJson(path: string, init?: RequestInit): Promise<unknown> {
  let response: Response;
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  if (init?.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers,
    });
  } catch {
    throw new ApiError(
      "AccessPilot could not reach the local API. Confirm the backend is running and try again.",
    );
  }

  const text = await response.text();
  let payload: unknown = {};
  if (text) {
    try {
      payload = JSON.parse(text) as unknown;
    } catch {
      payload = { message: text };
    }
  }

  if (!response.ok) {
    const payloadObject = object(payload);
    throw new ApiError(
      errorMessage(pick(payloadObject, "detail", "error", "message")) ||
        `The AccessPilot API returned ${response.status}.`,
      response.status,
    );
  }

  return payload;
}

export async function getConfig(): Promise<AppConfig> {
  const payload = object(await fetchJson("/api/config", { cache: "no-store" }));
  const data = object(pick(payload, "data", "config") ?? payload);
  const defaults = object(pick(data, "defaults", "default_values"));
  const defaultProvider = stringValue(
    pick(defaults, "provider", "llm_provider") ?? pick(data, "default_provider"),
    "gemini",
  );
  const defaultWcagLevel = stringValue(
    pick(defaults, "wcag_level", "wcag") ?? pick(data, "default_wcag_level"),
    "AA",
  );

  return {
    providers: normalizeOptions(pick(data, "providers", "llm_providers"), fallbackProviders),
    wcagLevels: normalizeOptions(pick(data, "wcag_levels", "wcag"), fallbackWcagLevels),
    defaultProvider,
    defaultWcagLevel,
    defaultScanDepth:
      numberValue(pick(defaults, "scan_depth") ?? pick(data, "default_scan_depth")) ?? 2,
    maxScanDepth: numberValue(pick(data, "max_scan_depth")) ?? 5,
    sampleTarget: stringValue(pick(data, "sample_target"), "sample://vulnerable-app"),
    localExecutionEnabled: booleanValue(
      pick(data, "local_execution_enabled", "localExecutionEnabled"),
    ),
    dependencyInstallEnabled: booleanValue(
      pick(data, "dependency_install_enabled", "dependencyInstallEnabled"),
    ),
    remoteMutationsEnabled: booleanValue(
      pick(data, "remote_mutations_enabled", "remoteMutationsEnabled"),
    ),
  };
}

export function normalizeRun(payload: unknown, fallbackId = ""): RunView {
  const envelope = object(payload);
  const data = object(pick(envelope, "data") ?? envelope);
  const root = object(pick(data, "run", "state") ?? data);
  const request = object(pick(root, "request"));
  const scan = object(pick(root, "scan_results", "scan", "findings"));
  const verification = object(pick(root, "verification", "verification_report"));
  const diff = object(pick(root, "diff", "patch", "changes"));
  const metrics = object(pick(root, "metrics", "counts", "summary"));
  const planValue = pick(root, "repair_plan", "plan");
  const plan = object(planValue);
  const issueValues = pick(root, "issues") ?? pick(scan, "issues", "findings", "violations");
  const verificationIssueValues = pick(root, "verification_issues");
  const planValues = Array.isArray(planValue)
    ? planValue
    : pick(plan, "items", "repairs", "actions", "changes");
  const changedValues =
    pick(root, "changed_files", "changes") ?? pick(diff, "files", "changed_files");
  const testValues =
    pick(root, "tests", "test_results") ?? pick(verification, "tests", "checks");
  const baselineIssues = (Array.isArray(issueValues) ? issueValues : []).map(normalizeIssue);
  const verificationIssues = (Array.isArray(verificationIssueValues) ? verificationIssueValues : [])
    .map(normalizeIssue);
  const issuesById = new Map(baselineIssues.map((issue) => [issue.id, issue]));
  for (const issue of verificationIssues) {
    issuesById.set(issue.id, { ...issuesById.get(issue.id), ...issue });
  }
  const unresolvedIssueIds = new Set(stringList(pick(root, "unresolved_issue_ids")));
  const issues = [...issuesById.values()].map((issue) =>
    unresolvedIssueIds.has(issue.id) && !["fixed", "regression"].includes(issue.status)
      ? { ...issue, status: "unresolved" as const, fixed: false }
      : issue,
  );
  const repairPlan = (Array.isArray(planValues) ? planValues : []).map(normalizePlanItem);
  const changedFiles = (Array.isArray(changedValues) ? changedValues : [])
    .map(normalizeChangedFile)
    .filter((file): file is ChangedFile => Boolean(file));
  const fileDiffSummary = changedFiles
    .map((file) => file.diff?.trim())
    .filter((value): value is string => Boolean(value))
    .join("\n\n");
  const tests = (Array.isArray(testValues) ? testValues : []).map(normalizeTest);
  const status = stringValue(pick(root, "status", "state"), "queued")
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, "_");
  const beforeCount =
    numberValue(
      pick(metrics, "before", "before_count", "original_issues") ??
        pick(root, "before_count", "original_issue_count") ??
        pick(verification, "baseline_issue_count"),
    ) ?? issues.length;
  const afterCount = numberValue(
      pick(metrics, "after", "after_count", "remaining_issues") ??
      pick(root, "after_count", "remaining_issue_count") ??
      pick(
        verification,
        "current_issue_count",
        "remaining_issue_count",
        "issue_count",
        "remaining_count",
        "fixed_issue_count",
      ),
  );
  const fixedCount =
    numberValue(
      pick(metrics, "fixed", "fixed_count") ??
        pick(root, "fixed_count") ??
        pick(verification, "fixed_issue_count"),
    ) ??
    (afterCount !== undefined
      ? Math.max(0, beforeCount - afterCount)
      : issues.filter((issue) => issue.fixed).length);
  const remainingCount =
    afterCount ?? Math.max(0, beforeCount - fixedCount);
  const approval = object(pick(root, "approval", "human_approval"));
  const rawDecision = stringValue(pick(approval, "decision") ?? pick(root, "approval_decision")).toLowerCase();
  const approvalDecision = ["approve", "approved"].includes(rawDecision)
    ? "approve"
    : ["revise", "revision_requested"].includes(rawDecision)
      ? "revise"
    : ["reject", "rejected"].includes(rawDecision)
      ? "reject"
      : undefined;
  const draft = normalizePrDraft(pick(root, "pr_draft", "pull_request", "pr"));
  const pullRequestUrl = stringValue(pick(root, "pull_request_url", "pr_url"));
  const prDraft = draft
    ? { ...draft, url: draft.url || pullRequestUrl || undefined }
    : pullRequestUrl
      ? { title: "Accessibility repairs", body: "", url: pullRequestUrl }
      : undefined;

  return {
    id: stringValue(pick(root, "id", "run_id", "session_id"), fallbackId),
    status,
    stage: stringValue(pick(root, "stage", "current_stage", "node", "current_node"), status),
    message:
      stringValue(pick(root, "message", "status_message", "detail")) ||
      (() => {
        const messages = pick(root, "messages");
        const last = Array.isArray(messages) ? object(messages.at(-1)) : {};
        return stringValue(
          pick(last, "text"),
          "AccessPilot is preparing the next workflow step.",
        );
      })(),
    progress: numberValue(pick(root, "progress", "progress_percent", "percent_complete")),
    target:
      stringValue(pick(request, "target")) ||
      stringValue(pick(root, "target_url", "repository", "source")),
    createdAt: stringValue(pick(root, "created_at", "createdAt")) || undefined,
    updatedAt: stringValue(pick(root, "updated_at", "updatedAt")) || undefined,
    issues,
    repairPlan,
    changedFiles,
    tests,
    preview: normalizePreview(pick(root, "preview", "previews"), root),
    prDraft,
    beforeCount,
    afterCount,
    fixedCount,
    remainingCount,
    diffSummary:
      stringValue(pick(root, "diff_summary") ?? pick(diff, "summary", "unified_diff")) ||
      fileDiffSummary ||
      undefined,
    diffHash:
      stringValue(
        pick(root, "diff_hash", "patch_hash") ??
          pick(diff, "hash", "sha256") ??
          pick(verification, "diff_sha256"),
      ) ||
      undefined,
    remotePublicationRequested: booleanValue(
      pick(request, "execute_pull_request", "remote_publication_requested"),
    ),
    approvalRequired:
      booleanValue(pick(root, "approval_required", "requires_approval")) ||
      ["waiting_for_approval", "awaiting_approval", "approval_required", "ready_for_review"].includes(status),
    approvalDecision,
    error:
      errorMessage(pick(root, "error", "failure")) ||
      (status === "failed" ? stringValue(pick(root, "message", "detail")) : undefined),
  };
}

export async function createRun(request: RunRequest): Promise<RunView> {
  const payload = await fetchJson("/api/runs", {
    method: "POST",
    body: JSON.stringify(request),
  });
  return normalizeRun(payload);
}

export async function getRun(runId: string): Promise<RunView> {
  const payload = await fetchJson(`/api/runs/${encodeURIComponent(runId)}`, {
    cache: "no-store",
  });
  return normalizeRun(payload, runId);
}

export async function submitApproval(
  runId: string,
  request: ApprovalRequest,
): Promise<RunView> {
  const payload = await fetchJson(`/api/runs/${encodeURIComponent(runId)}/approval`, {
    method: "POST",
    body: JSON.stringify(request),
  });
  return normalizeRun(payload, runId);
}
