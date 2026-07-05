import { useCallback, useEffect, useState } from "react";

import { createRun, getConfig, getRun, submitApproval } from "./api";
import { IntakeForm } from "./components/IntakeForm";
import {
  CheckIcon,
  FileCodeIcon,
  RadarIcon,
  ShieldCheckIcon,
  SparkIcon,
} from "./components/Icons";
import {
  ErrorNotice,
  RunEmptyState,
  RunLoadingState,
  RunStatus,
} from "./components/RunStatus";
import {
  FindingsPanel,
  RepairPlanPanel,
  ResultsSummary,
} from "./components/ResultsPanels";
import {
  PrReviewPanel,
  PreviewPanel,
  VerificationPanel,
} from "./components/ReviewPanels";
import type { AppConfig, ApprovalRequest, RunRequest } from "./types";
import type { RunView } from "./types";

const fallbackConfig: AppConfig = {
  providers: [
    { value: "deterministic", label: "Deterministic (no LLM)" },
    { value: "gemini", label: "Gemini" },
    { value: "openai", label: "OpenAI" },
  ],
  wcagLevels: [
    { value: "A", label: "WCAG 2.2 Level A" },
    { value: "AA", label: "WCAG 2.2 Level AA" },
    { value: "AAA", label: "WCAG 2.2 Level AAA" },
  ],
  defaultProvider: "gemini",
  defaultWcagLevel: "AA",
  defaultScanDepth: 2,
  maxScanDepth: 5,
  sampleTarget: "sample://vulnerable-app",
  localExecutionEnabled: false,
  dependencyInstallEnabled: false,
  remoteMutationsEnabled: false,
};

const processSteps = [
  {
    title: "Render & scan",
    description: "Exercise real routes with a browser and deterministic axe checks.",
    icon: RadarIcon,
  },
  {
    title: "Trace to source",
    description: "Map selectors back to components, templates, styles, and tests.",
    icon: FileCodeIcon,
  },
  {
    title: "Repair with restraint",
    description: "Prefer semantic, minimal edits inside an isolated working copy.",
    icon: SparkIcon,
  },
  {
    title: "Verify & hand off",
    description: "Re-scan, run project checks, and wait for your explicit approval.",
    icon: ShieldCheckIcon,
  },
];

export function App() {
  const [config, setConfig] = useState(fallbackConfig);
  const [run, setRun] = useState<RunView | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [submittingDecision, setSubmittingDecision] = useState(false);
  const [apiError, setApiError] = useState("");
  const [apiAvailable, setApiAvailable] = useState<boolean | null>(null);
  const [configWarning, setConfigWarning] = useState("");
  const [announcement, setAnnouncement] = useState("AccessPilot workspace ready.");

  useEffect(() => {
    let active = true;
    getConfig()
      .then((nextConfig) => {
        if (active) {
          setConfig(nextConfig);
          setApiAvailable(true);
        }
      })
      .catch(() => {
        if (active) {
          setApiAvailable(false);
          setConfigWarning(
            "Backend configuration is unavailable, so safe local defaults are shown.",
          );
        }
      });
    return () => {
      active = false;
    };
  }, []);

  const handleSubmit = async (request: RunRequest) => {
    setSubmitting(true);
    setApiError("");
    setAnnouncement(`Starting an accessibility assessment for ${request.target}.`);
    try {
      const nextRun = await createRun(request);
      if (!nextRun.id) {
        throw new Error("The API accepted the request but did not return a run identifier.");
      }
      setRun({
        ...nextRun,
        target: nextRun.target || request.target,
        remotePublicationRequested: request.execute_pull_request,
      });
      setApiAvailable(true);
      setAnnouncement(`Run ${nextRun.id} started. ${nextRun.message}`);
      window.requestAnimationFrame(() => {
        document.getElementById("run-workspace")?.focus();
      });
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "The assessment could not be started.";
      setApiError(message);
      setAnnouncement(message);
    } finally {
      setSubmitting(false);
    }
  };

  const refreshRun = useCallback(async (showBusyState = true) => {
    if (!run?.id) return;
    if (showBusyState) setRefreshing(true);
    try {
      const nextRun = await getRun(run.id);
      setRun(nextRun);
      setApiAvailable(true);
      if (showBusyState) setAnnouncement(`Run status updated: ${nextRun.message}`);
    } catch (error) {
      if (showBusyState) {
        const message = error instanceof Error ? error.message : "Run status could not be refreshed.";
        setApiError(message);
        setAnnouncement(message);
      }
    } finally {
      if (showBusyState) setRefreshing(false);
    }
  }, [run?.id]);

  useEffect(() => {
    if (!run?.id) return;
    const pausePolling = [
      "completed",
      "failed",
      "cancelled",
      "pr_created",
      "waiting_for_approval",
      "awaiting_approval",
      "approval_required",
      "ready_for_review",
    ].includes(run.status) || (run.status === "rejected" && run.approvalDecision !== "revise");
    if (pausePolling) return;

    let polling = false;
    const timer = window.setInterval(() => {
      if (polling) return;
      polling = true;
      void refreshRun(false).finally(() => {
        polling = false;
      });
    }, 2500);
    return () => window.clearInterval(timer);
  }, [refreshRun, run?.id, run?.status]);

  useEffect(() => {
    if (run) {
      setAnnouncement(`Run ${run.id}: ${run.stage}. ${run.message}`);
    }
  }, [run?.id, run?.message, run?.stage]);

  const handleDecision = async (request: ApprovalRequest) => {
    if (!run?.id) return;
    const remotePublicationRequested = run.remotePublicationRequested;
    setSubmittingDecision(true);
    setApiError("");
    setAnnouncement(
      request.decision === "approve"
        ? remotePublicationRequested
          ? "Recording approval to publish the reviewed diff."
          : "Recording dry-run approval for the reviewed diff."
        : request.decision === "revise"
          ? "Recording a revision request for the reviewed diff."
          : "Recording rejection and stopping this run.",
    );
    try {
      const nextRun = await submitApproval(run.id, request);
      setApiAvailable(true);
      setRun((current) => {
        if (!current) return nextRun;
        return {
          ...current,
          ...nextRun,
          id: nextRun.id || current.id,
          target: nextRun.target || current.target,
          issues: nextRun.issues.length > 0 ? nextRun.issues : current.issues,
          repairPlan:
            nextRun.repairPlan.length > 0 ? nextRun.repairPlan : current.repairPlan,
          changedFiles:
            nextRun.changedFiles.length > 0 ? nextRun.changedFiles : current.changedFiles,
          tests: nextRun.tests.length > 0 ? nextRun.tests : current.tests,
          preview: {
            ...current.preview,
            ...Object.fromEntries(
              Object.entries(nextRun.preview).filter(([, value]) => Boolean(value)),
            ),
          },
          prDraft: nextRun.prDraft || current.prDraft,
          diffSummary: nextRun.diffSummary || current.diffSummary,
          diffHash: nextRun.diffHash || current.diffHash,
          remotePublicationRequested:
            nextRun.remotePublicationRequested || current.remotePublicationRequested,
          approvalDecision: nextRun.approvalDecision || request.decision,
        };
      });
      setAnnouncement(
        request.decision === "approve"
          ? remotePublicationRequested
            ? "Approval recorded. AccessPilot is preparing the reviewed branch and pull request."
            : "Dry-run approval recorded. No remote branch or pull request will be created."
          : request.decision === "revise"
            ? "Revision requested. AccessPilot is returning the reviewed diff to the repair loop."
            : "Rejection recorded. The run is stopped and nothing will be published.",
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : "The review decision could not be recorded.";
      setApiError(message);
      setAnnouncement(message);
    } finally {
      setSubmittingDecision(false);
    }
  };

  return (
    <div className="app-shell">
      <header className="site-header">
        <a className="brand" href="#main-content" aria-label="AccessPilot home">
          <span className="brand-mark"><RadarIcon /></span>
          <span className="brand-name">AccessPilot</span>
          <span className="brand-tag">Autonomous Accessibility Repair Agent</span>
        </a>
        <div className="header-meta" role="status" aria-label="Application status">
          <span
            className={`status-dot${apiAvailable === false ? " is-offline" : apiAvailable === null ? " is-checking" : ""}`}
            aria-hidden="true"
          />
          {apiAvailable === true
            ? "Backend connected"
            : apiAvailable === false
              ? "Local defaults"
              : "Checking local API"}
        </div>
      </header>

      <div className="visually-hidden" aria-live="polite" aria-atomic="true">
        {announcement}
      </div>

      <main id="main-content">
        <section className="hero" aria-labelledby="page-title">
          <div className="hero-copy">
            <p className="eyebrow"><span>AccessPilot workspace</span></p>
            <h1 id="page-title">Autonomous Accessibility Repair Agent</h1>
            <p className="hero-summary">
              Scan rendered experiences, trace barriers to source, and review verified
              accessibility repairs before anything is published.
            </p>
          </div>
          <div className="hero-proof" role="list" aria-label="AccessPilot guardrails">
            <span role="listitem"><CheckIcon /> Deterministic evidence</span>
            <span role="listitem"><CheckIcon /> Isolated source edits</span>
            <span role="listitem"><CheckIcon /> Human-approved changes</span>
          </div>
          <figure className="hero-banner">
            <img
              src="/banner_image.png"
              width="1006"
              height="562"
              alt="AccessPilot workflow showing an inaccessible web page scanned, repaired, verified, and prepared as a pull request."
            />
          </figure>
        </section>

        <div className="launch-grid">
          <div>
            {configWarning ? <p className="configuration-note">{configWarning}</p> : null}
            <IntakeForm
              config={config}
              submitting={submitting}
              disabled={submitting}
              onSubmit={handleSubmit}
            />
          </div>

          <aside className="process-card" aria-labelledby="process-title">
            <div className="section-kicker"><span>Repair loop</span></div>
            <h2 id="process-title">Evidence all the way down</h2>
            <ol className="process-list">
              {processSteps.map((step, index) => {
                const Icon = step.icon;
                return (
                  <li key={step.title}>
                    <span className="step-number">0{index + 1}</span>
                    <span className="step-icon"><Icon /></span>
                    <span>
                      <strong>{step.title}</strong>
                      <small>{step.description}</small>
                    </span>
                  </li>
                );
              })}
            </ol>
            <div className="process-note">
              <ShieldCheckIcon />
              <p>
                <strong>Safe by construction</strong>
                Push and pull-request tools remain unavailable until the approval gate.
              </p>
            </div>
          </aside>
        </div>

        {apiError ? <ErrorNotice message={apiError} onDismiss={() => setApiError("")} /> : null}

        <div
          id="run-workspace"
          className="run-workspace"
          tabIndex={-1}
          aria-busy={submitting || refreshing}
        >
          {run ? (
            <>
              <RunStatus
                run={run}
                refreshing={refreshing}
                onRefresh={() => void refreshRun(true)}
              />
              <nav className="workspace-nav" aria-label="Run workspace sections">
                <a href="#scan-results"><span>01</span> Findings</a>
                <a href="#repair-plan"><span>02</span> Repair plan</a>
                <a href="#previews"><span>03</span> Preview</a>
                <a href="#pr-review"><span>04</span> PR review</a>
              </nav>
              <ResultsSummary run={run} />
              <div className="workspace-grid">
                <FindingsPanel key={`findings-${run.id}`} run={run} />
                <RepairPlanPanel run={run} />
              </div>
              <PreviewPanel run={run} />
              <div className="review-grid">
                <VerificationPanel run={run} />
                <PrReviewPanel
                  key={`review-${run.id}`}
                  run={run}
                  submitting={submittingDecision}
                  onDecision={handleDecision}
                />
              </div>
            </>
          ) : submitting ? (
            <RunLoadingState />
          ) : (
            <RunEmptyState />
          )}
        </div>
      </main>

      <footer className="site-footer">
        <p>AccessPilot keeps uncertainty visible and every change traceable.</p>
        <a href="#main-content">Back to top</a>
      </footer>
    </div>
  );
}
