export type Severity = "critical" | "serious" | "moderate" | "minor" | "unknown";

export type ApprovalDecision = "approve" | "revise" | "reject";

export type IssueStatus = "open" | "planned" | "fixed" | "unresolved" | "regression";

export interface SelectOption {
  value: string;
  label: string;
  description?: string;
}

export interface AppConfig {
  providers: SelectOption[];
  wcagLevels: SelectOption[];
  defaultProvider: string;
  defaultWcagLevel: string;
  defaultScanDepth: number;
  maxScanDepth: number;
  sampleTarget: string;
  localExecutionEnabled: boolean;
  dependencyInstallEnabled: boolean;
  remoteMutationsEnabled: boolean;
}

export interface RunRequest {
  target: string;
  branch_name?: string;
  wcag_level: string;
  routes: string[];
  scan_depth: number;
  llm_provider: string;
  authentication_instructions?: string;
  app_url?: string;
  allow_local_execution: boolean;
  allow_dependency_install: boolean;
  execute_pull_request: boolean;
}

export interface Issue {
  id: string;
  title: string;
  severity: Severity;
  wcag: string[];
  route: string;
  selector: string;
  explanation: string;
  suggestedFix: string;
  confidence?: number;
  evidence?: string;
  screenshotUrl?: string;
  status: IssueStatus;
  fixed: boolean;
}

export interface RepairPlanItem {
  id: string;
  title: string;
  issueIds: string[];
  strategy: string;
  files: string[];
  confidence?: number;
  risk: "low" | "medium" | "high" | "unknown";
  status: string;
}

export interface ChangedFile {
  path: string;
  additions?: number;
  deletions?: number;
  summary?: string;
  diff?: string;
}

export interface TestResult {
  name: string;
  status: "passed" | "failed" | "skipped" | "running" | "unknown";
  command?: string;
  detail?: string;
  duration?: string;
}

export interface PreviewDetails {
  originalUrl?: string;
  fixedUrl?: string;
  originalScreenshotUrl?: string;
  fixedScreenshotUrl?: string;
}

export interface PullRequestDraft {
  title: string;
  body: string;
  commitMessage?: string;
  branch?: string;
  url?: string;
}

export interface RunView {
  id: string;
  status: string;
  stage: string;
  message: string;
  progress?: number;
  target: string;
  createdAt?: string;
  updatedAt?: string;
  issues: Issue[];
  repairPlan: RepairPlanItem[];
  changedFiles: ChangedFile[];
  tests: TestResult[];
  preview: PreviewDetails;
  prDraft?: PullRequestDraft;
  beforeCount: number;
  afterCount?: number;
  fixedCount: number;
  remainingCount: number;
  diffSummary?: string;
  diffHash?: string;
  remotePublicationRequested: boolean;
  approvalRequired: boolean;
  approvalDecision?: ApprovalDecision;
  error?: string;
}

export interface ApprovalRequest {
  decision: ApprovalDecision;
  reviewer_note?: string;
  expected_diff_sha256: string;
}

export interface ApiEnvelope<T> {
  data: T;
  message?: string;
}
