import type { RunView } from "../types";
import { AlertIcon, CheckIcon, RefreshIcon } from "./Icons";

interface RunStatusProps {
  run: RunView;
  refreshing: boolean;
  onRefresh: () => void;
}

const stageOrder = [
  ["intake", "Input checked"],
  ["load", "Source loaded"],
  ["scan", "Pages scanned"],
  ["plan", "Repair planned"],
  ["repair", "Patch applied"],
  ["verify", "Fix verified"],
  ["approval", "Review ready"],
] as const;

const stageIndex = (stage: string) => {
  const normalized = stage.toLowerCase();
  const index = stageOrder.findIndex(([key]) => normalized.includes(key));
  if (["completed", "pr_created", "approved"].includes(normalized)) return stageOrder.length;
  return Math.max(0, index);
};

const pretty = (value: string) =>
  value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());

export function RunStatus({ run, refreshing, onRefresh }: RunStatusProps) {
  const currentIndex = stageIndex(run.stage || run.status);
  const failed = run.status === "failed";
  const revisionInProgress = run.status === "rejected" && run.approvalDecision === "revise";
  const complete = ["completed", "pr_created", "rejected", "cancelled"].includes(run.status) && !revisionInProgress;
  const progress = run.progress ?? Math.round((currentIndex / stageOrder.length) * 100);

  return (
    <section className={`run-status-card${failed ? " is-error" : ""}`} aria-labelledby="run-status-title">
      <div className="run-status-head">
        <div>
          <p className="section-kicker"><span>Run {run.id.slice(0, 8) || "pending"}</span></p>
          <h2 id="run-status-title">{pretty(run.stage || run.status)}</h2>
          <p>{run.error || run.message}</p>
        </div>
        <div className="run-status-actions">
          <span className={`run-pill run-pill-${failed ? "failed" : complete ? "complete" : "active"}`}>
            {failed ? <AlertIcon /> : complete ? <CheckIcon /> : <span className="pulse-dot" />}
            {revisionInProgress ? "Revision requested" : pretty(run.status)}
          </span>
          <button className="icon-button" type="button" onClick={onRefresh} disabled={refreshing}>
            <RefreshIcon className={refreshing ? "is-spinning" : ""} />
            <span className="visually-hidden">Refresh run status</span>
          </button>
        </div>
      </div>

      <div
        className="progress-track"
        role="progressbar"
        aria-label="Workflow completion"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.min(100, Math.max(0, progress))}
      >
        <span style={{ width: `${Math.min(100, Math.max(2, progress))}%` }} />
      </div>

      <ol className="stage-list" aria-label="Workflow progress">
        {stageOrder.map(([key, label], index) => {
          const state = index < currentIndex ? "complete" : index === currentIndex ? "current" : "upcoming";
          return (
            <li key={key} className={`stage-${state}`} aria-current={state === "current" ? "step" : undefined}>
              <span>{state === "complete" ? <CheckIcon /> : index + 1}</span>
              {label}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export function RunEmptyState() {
  return (
    <section className="run-empty" aria-labelledby="empty-run-title">
      <span className="empty-orbit" aria-hidden="true"><span /></span>
      <div>
        <p className="section-kicker"><span>Workspace ready</span></p>
        <h2 id="empty-run-title">Your evidence trail will appear here</h2>
        <p>
          Start an assessment to follow the scan, repair plan, local patch, verification,
          and approval gate in one place.
        </p>
      </div>
    </section>
  );
}

export function RunLoadingState() {
  return (
    <section className="run-loading" role="status" aria-live="polite">
      <span className="loading-radar" aria-hidden="true"><span /></span>
      <div>
        <p className="section-kicker"><span>Creating run</span></p>
        <h2>Preparing the isolated workspace</h2>
        <p>AccessPilot is validating the target and establishing a traceable run.</p>
      </div>
    </section>
  );
}

export function ErrorNotice({ message, onDismiss }: { message: string; onDismiss: () => void }) {
  return (
    <div className="error-notice" role="alert">
      <AlertIcon />
      <p><strong>AccessPilot needs attention</strong>{message}</p>
      <button type="button" onClick={onDismiss}>Dismiss</button>
    </div>
  );
}
