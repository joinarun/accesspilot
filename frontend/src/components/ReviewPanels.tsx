import { useId, useRef, useState } from "react";

import type { ApprovalDecision, ApprovalRequest, RunView } from "../types";
import {
  AlertIcon,
  CheckIcon,
  ExternalLinkIcon,
  FileCodeIcon,
  ShieldCheckIcon,
  XIcon,
} from "./Icons";

const pretty = (value: string) =>
  value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());

function safeUrl(value?: string) {
  if (!value) return undefined;
  try {
    const parsed = new URL(value, window.location.origin);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : undefined;
  } catch {
    return undefined;
  }
}

function PreviewCard({
  label,
  url,
  screenshotUrl,
  issueCount,
  tone,
}: {
  label: string;
  url?: string;
  screenshotUrl?: string;
  issueCount?: number;
  tone: "before" | "after";
}) {
  const previewUrl = safeUrl(url);
  const imageUrl = safeUrl(screenshotUrl);

  return (
    <article className={`preview-card preview-${tone}`}>
      <header>
        <span>{label}</span>
        <strong>{issueCount === undefined ? "—" : issueCount}</strong>
        <small>findings</small>
      </header>
      <div className="preview-frame">
        {imageUrl ? (
          <img
            src={imageUrl}
            alt={`${label} application screenshot`}
            loading="lazy"
            decoding="async"
          />
        ) : (
          <div className="preview-placeholder" aria-label={`${label} preview is not available yet`}>
            <span aria-hidden="true"><span /><span /><span /></span>
            <p>Preview starts after the application is rendered.</p>
          </div>
        )}
      </div>
      <footer>
        <code title={url}>{url || "Preview URL pending"}</code>
        {previewUrl ? (
          <a className="button button-secondary" href={previewUrl} target="_blank" rel="noreferrer">
            Open preview <ExternalLinkIcon />
          </a>
        ) : (
          <span className="button button-secondary is-disabled" aria-disabled="true">
            Preview pending
          </span>
        )}
      </footer>
    </article>
  );
}

export function PreviewPanel({ run }: { run: RunView }) {
  return (
    <section id="previews" className="workspace-panel preview-panel" aria-labelledby="preview-title">
      <header className="panel-heading">
        <div>
          <p className="section-kicker"><span>Before / after</span></p>
          <h2 id="preview-title">Compare the rendered experience</h2>
          <p>Open each local preview for keyboard and screen-reader review.</p>
        </div>
        {run.afterCount !== undefined ? (
          <span className="delta-badge">
            {Math.max(0, run.beforeCount - run.afterCount)} fewer findings
          </span>
        ) : null}
      </header>
      <div className="preview-grid">
        <PreviewCard
          label="Original"
          url={run.preview.originalUrl}
          screenshotUrl={run.preview.originalScreenshotUrl}
          issueCount={run.beforeCount}
          tone="before"
        />
        <PreviewCard
          label="Fixed working copy"
          url={run.preview.fixedUrl}
          screenshotUrl={run.preview.fixedScreenshotUrl}
          issueCount={run.afterCount}
          tone="after"
        />
      </div>
    </section>
  );
}

function TestStatusIcon({ status }: { status: string }) {
  if (status === "passed") return <CheckIcon />;
  if (status === "failed") return <XIcon />;
  if (status === "running") return <span className="spinner" aria-hidden="true" />;
  return <span aria-hidden="true">–</span>;
}

export function VerificationPanel({ run }: { run: RunView }) {
  return (
    <section className="workspace-panel verification-panel" aria-labelledby="verification-title">
      <header className="panel-heading">
        <div>
          <p className="section-kicker"><span>Change evidence</span></p>
          <h2 id="verification-title">Diff & test summary</h2>
          <p>Only changes and commands associated with this run are shown.</p>
        </div>
      </header>

      <div className="verification-block">
        <h3>Changed files <span>{run.changedFiles.length}</span></h3>
        {run.changedFiles.length > 0 ? (
          <ul className="changed-file-list">
            {run.changedFiles.map((file) => (
              <li key={file.path}>
                <FileCodeIcon />
                <span><code>{file.path}</code>{file.summary ? <small>{file.summary}</small> : null}</span>
                <span className="line-delta" aria-label={`${file.additions ?? 0} additions and ${file.deletions ?? 0} deletions`}>
                  <b>+{file.additions ?? 0}</b> <i>−{file.deletions ?? 0}</i>
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="inline-empty">No source files have changed yet.</p>
        )}
      </div>

      {run.diffSummary ? (
        <details className="diff-details">
          <summary>Read diff summary</summary>
          <pre><code>{run.diffSummary}</code></pre>
        </details>
      ) : null}

      <div className="verification-block">
        <h3>Verification checks <span>{run.tests.length}</span></h3>
        {run.tests.length > 0 ? (
          <ul className="test-list">
            {run.tests.map((test, index) => (
              <li key={`${test.name}-${index}`} className={`test-${test.status}`}>
                <span className="test-icon"><TestStatusIcon status={test.status} /></span>
                <span>
                  <strong>{test.name}</strong>
                  {test.command ? <code>{test.command}</code> : null}
                  {test.detail ? <small>{test.detail}</small> : null}
                </span>
                <span>{test.duration || pretty(test.status)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="inline-empty">Project checks will appear after patch validation.</p>
        )}
      </div>
    </section>
  );
}

interface PrReviewPanelProps {
  run: RunView;
  submitting: boolean;
  onDecision: (request: ApprovalRequest) => Promise<void>;
}

export function PrReviewPanel({ run, submitting, onDecision }: PrReviewPanelProps) {
  const formId = useId();
  const decisionPanel = useRef<HTMLDivElement>(null);
  const [decision, setDecision] = useState<ApprovalDecision | null>(null);
  const [comment, setComment] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const draft = run.prDraft;
  const prUrl = safeUrl(draft?.url);
  const approvalBoundToDiff = Boolean(run.diffHash);
  const remotePublicationRequested = run.remotePublicationRequested;

  const chooseDecision = (nextDecision: ApprovalDecision) => {
    setDecision(nextDecision);
    setConfirmed(false);
    window.requestAnimationFrame(() => decisionPanel.current?.focus());
  };

  const submit = async () => {
    if (
      !decision ||
      !approvalBoundToDiff ||
      (decision === "approve" && (!confirmed || !approvalBoundToDiff))
    ) return;
    await onDecision({
      decision,
      reviewer_note: comment.trim() || undefined,
      expected_diff_sha256: run.diffHash || "",
    });
    setDecision(null);
    setComment("");
    setConfirmed(false);
  };

  const confirmationTitle =
    decision === "approve"
      ? remotePublicationRequested
        ? "Confirm approval and publication"
        : "Confirm dry-run approval"
      : decision === "revise"
        ? "Confirm revision request"
        : "Confirm rejection";
  const confirmationCopy =
    decision === "approve"
      ? remotePublicationRequested
        ? "This authorizes AccessPilot to push the reviewed branch and open one pull request for the exact diff shown above."
        : "This records approval of the exact reviewed diff. This run is a dry run, so no branch will be pushed and no pull request will be created."
      : decision === "revise"
        ? "This returns the reviewed diff to the repair loop with your note. Nothing will be published while the requested revision is prepared and verified."
        : "This stops the run. No remote branch or pull request will be created.";
  const reviewPlaceholder =
    decision === "approve"
      ? remotePublicationRequested
        ? "Context for the pull request…"
        : "Why this verified diff is acceptable…"
      : decision === "revise"
        ? "Describe the revision you need…"
        : "Why should this run stop?";

  return (
    <section id="pr-review" className="workspace-panel pr-panel" aria-labelledby="pr-review-title">
      <header className="panel-heading">
        <div>
          <p className="section-kicker"><span>Human review gate</span></p>
          <h2 id="pr-review-title">
            {remotePublicationRequested ? "Pull request review" : "Verified change review"}
          </h2>
          <p>
            {remotePublicationRequested
              ? "Review the exact proposal before any branch is pushed remotely."
              : "Review the exact proposal. This run is a dry run and cannot push a branch or create a pull request."}
          </p>
        </div>
        <span className={`gate-badge${run.approvalRequired ? " is-ready" : ""}`}>
          <ShieldCheckIcon /> {run.approvalRequired ? "Decision required" : "Gate locked"}
        </span>
      </header>

      {remotePublicationRequested && prUrl ? (
        <div className="pr-created">
          <CheckIcon />
          <p><strong>Pull request created</strong>The approved change is now ready for repository review.</p>
          <a href={prUrl} target="_blank" rel="noreferrer">Open pull request <ExternalLinkIcon /></a>
        </div>
      ) : null}

      <dl className="pr-metadata">
        <div>
          <dt>Branch</dt>
          <dd><code>{draft?.branch || "Pending verified patch"}</code></dd>
        </div>
        <div>
          <dt>Commit</dt>
          <dd>{draft?.commitMessage || "Commit message will appear after verification."}</dd>
        </div>
        {run.diffHash ? (
          <div>
            <dt>Diff fingerprint</dt>
            <dd><code>{run.diffHash}</code></dd>
          </div>
        ) : null}
      </dl>

      <div className="pr-copy-block">
        <span>Proposed title</span>
        <h3>{draft?.title || "PR draft is not available yet"}</h3>
      </div>
      <div className="pr-copy-block">
        <span>Proposed description</span>
        {draft?.body ? <pre>{draft.body}</pre> : <p>AccessPilot will summarize fixes, WCAG references, verification, and remaining issues here.</p>}
      </div>

      {run.approvalDecision ? (
        <div className={`decision-recorded decision-${run.approvalDecision}`} role="status">
          {run.approvalDecision === "approve" ? <CheckIcon /> : run.approvalDecision === "revise" ? <AlertIcon /> : <XIcon />}
          <p>
            <strong>
              {run.approvalDecision === "approve"
                ? remotePublicationRequested
                  ? "Remote publication approved"
                  : "Dry-run approval recorded"
                : run.approvalDecision === "revise"
                  ? "Revision requested"
                  : "Run rejected safely"}
            </strong>
            {run.approvalDecision === "approve"
              ? remotePublicationRequested
                ? "AccessPilot may publish only the reviewed diff fingerprint above."
                : "The reviewed diff was accepted without authorizing a push or pull request."
              : run.approvalDecision === "revise"
                ? "The repair loop will prepare and verify a new diff before asking for another decision."
                : "No remote branch or pull request will be created for this run."}
          </p>
        </div>
      ) : null}

      {run.approvalRequired && (!run.approvalDecision || run.approvalDecision === "revise") ? (
        <div className="decision-zone">
          {!approvalBoundToDiff ? (
            <p id={`${formId}-missing-hash`} className="fingerprint-warning" role="status">
              Review decisions are disabled until the backend supplies the verified diff fingerprint.
            </p>
          ) : null}
          <div className="decision-actions" aria-label="Review decision">
            <button
              className="button button-primary"
              type="button"
              onClick={() => chooseDecision("approve")}
              disabled={submitting || !approvalBoundToDiff}
              aria-describedby={!approvalBoundToDiff ? `${formId}-missing-hash` : undefined}
            >
              <CheckIcon /> {remotePublicationRequested ? "Approve & create PR" : "Approve changes (dry run)"}
            </button>
            <button
              className="button button-secondary"
              type="button"
              onClick={() => chooseDecision("revise")}
              disabled={submitting || !approvalBoundToDiff}
              aria-describedby={!approvalBoundToDiff ? `${formId}-missing-hash` : undefined}
            >
              <AlertIcon /> Request revision
            </button>
            <button
              className="button button-danger"
              type="button"
              onClick={() => chooseDecision("reject")}
              disabled={submitting || !approvalBoundToDiff}
              aria-describedby={!approvalBoundToDiff ? `${formId}-missing-hash` : undefined}
            >
              <XIcon /> Reject changes
            </button>
          </div>

          {decision ? (
            <div ref={decisionPanel} className={`decision-confirm decision-confirm-${decision}`} tabIndex={-1}>
              <h3>{confirmationTitle}</h3>
              <p>{confirmationCopy}</p>

              <div className="field">
                <label htmlFor={`${formId}-comment`}>Review note <span>(optional)</span></label>
                <textarea
                  id={`${formId}-comment`}
                  rows={3}
                  value={comment}
                  onChange={(event) => setComment(event.target.value)}
                  placeholder={reviewPlaceholder}
                />
              </div>

              {decision === "approve" ? (
                <label className="confirmation-check">
                  <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
                  <span>I reviewed the findings, diff, remaining issues, and test results for this run.</span>
                </label>
              ) : null}

              <div className="decision-confirm-actions">
                <button
                  className={`button ${decision === "approve" ? "button-primary" : decision === "revise" ? "button-secondary" : "button-danger"}`}
                  type="button"
                  onClick={() => void submit()}
                  disabled={
                    submitting ||
                    !approvalBoundToDiff ||
                    (decision === "approve" && (!confirmed || !approvalBoundToDiff))
                  }
                >
                  {submitting ? (
                    <><span className="spinner" /> Recording decision…</>
                  ) : decision === "approve" ? (
                    remotePublicationRequested ? "Approve and publish" : "Approve dry run"
                  ) : decision === "revise" ? (
                    "Request revision"
                  ) : (
                    "Confirm rejection"
                  )}
                </button>
                <button className="button button-secondary" type="button" onClick={() => setDecision(null)} disabled={submitting}>
                  Cancel
                </button>
              </div>
            </div>
          ) : null}
        </div>
      ) : !run.approvalDecision && !prUrl ? (
        <div className="gate-waiting">
          <AlertIcon />
          <p>
            <strong>Review is not open yet</strong>
            Verification must finish before the reviewed diff can be approved, revised, or rejected.
          </p>
        </div>
      ) : null}
    </section>
  );
}
