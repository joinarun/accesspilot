import { useId, useMemo, useState } from "react";

import type { Issue, IssueStatus, RunView, Severity } from "../types";
import { AlertIcon, CheckIcon, ExternalLinkIcon, FileCodeIcon, SparkIcon } from "./Icons";

const severityOrder: Severity[] = ["critical", "serious", "moderate", "minor", "unknown"];
const issueStatuses: IssueStatus[] = ["open", "planned", "fixed", "unresolved", "regression"];

const pretty = (value: string) =>
  value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());

function percent(value?: number) {
  return value === undefined ? "Not scored" : `${Math.round(value * 100)}%`;
}

function safeUrl(value?: string) {
  if (!value) return undefined;
  try {
    const parsed = new URL(value, window.location.origin);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : undefined;
  } catch {
    return undefined;
  }
}

function scanHasRun(run: RunView) {
  const value = `${run.stage} ${run.status}`.toLowerCase();
  return [
    "scan",
    "normal",
    "map",
    "plan",
    "repair",
    "patch",
    "verify",
    "preview",
    "approval",
    "review",
    "complete",
    "failed",
  ].some((stage) => value.includes(stage));
}

export function ResultsSummary({ run }: { run: RunView }) {
  const improvement =
    run.afterCount !== undefined && run.beforeCount > 0
      ? Math.round(((run.beforeCount - run.afterCount) / run.beforeCount) * 100)
      : undefined;
  const metrics = [
    { label: "Original findings", value: run.beforeCount, detail: "deterministic baseline" },
    { label: "Repaired", value: run.fixedCount, detail: "no longer reproduced" },
    {
      label: "Remaining",
      value: run.afterCount ?? "—",
      detail: run.afterCount === undefined ? "verification pending" : "after verification",
    },
    {
      label: "Improvement",
      value: improvement === undefined ? "—" : `${improvement}%`,
      detail: improvement === undefined ? "awaiting comparison" : "issue-count reduction",
    },
  ];

  return (
    <section className="metrics-strip" aria-label="Before and after summary">
      {metrics.map((metric) => (
        <div key={metric.label}>
          <span>{metric.label}</span>
          <strong>{metric.value}</strong>
          <small>{metric.detail}</small>
        </div>
      ))}
    </section>
  );
}

interface IssueCardProps {
  issue: Issue;
}

function IssueCard({ issue }: IssueCardProps) {
  const screenshotUrl = safeUrl(issue.screenshotUrl);
  return (
    <article className={`issue-card severity-${issue.severity}`}>
      <div className="issue-card-head">
        <span className="severity-badge">
          <span aria-hidden="true" /> {pretty(issue.severity)}
        </span>
        <span className={`issue-status-badge status-${issue.status}`}>
          {issue.status === "fixed" ? <CheckIcon /> : null}
          {issue.status === "planned" ? <SparkIcon /> : null}
          {["unresolved", "regression"].includes(issue.status) ? <AlertIcon /> : null}
          {pretty(issue.status)}
        </span>
      </div>
      <h3>{issue.title}</h3>
      <p className="issue-explanation">{issue.explanation}</p>

      <dl className="issue-meta">
        <div>
          <dt>Route</dt>
          <dd><code>{issue.route}</code></dd>
        </div>
        <div>
          <dt>Selector</dt>
          <dd><code>{issue.selector}</code></dd>
        </div>
        <div>
          <dt>Confidence</dt>
          <dd>{percent(issue.confidence)}</dd>
        </div>
      </dl>

      {issue.wcag.length > 0 ? (
        <div className="tag-list" aria-label="WCAG references">
          {issue.wcag.slice(0, 6).map((tag) => <span key={tag}>{tag}</span>)}
        </div>
      ) : null}

      <div className="fix-guidance">
        <SparkIcon />
        <p><strong>Suggested strategy</strong>{issue.suggestedFix}</p>
      </div>

      {issue.evidence || screenshotUrl ? (
        <details className="evidence-details">
          <summary>Inspect evidence</summary>
          {issue.evidence ? <pre><code>{issue.evidence}</code></pre> : null}
          {screenshotUrl ? (
            <a href={screenshotUrl} target="_blank" rel="noreferrer">
              Open captured screenshot <ExternalLinkIcon />
            </a>
          ) : null}
        </details>
      ) : null}
    </article>
  );
}

export function FindingsPanel({ run }: { run: RunView }) {
  const filterId = useId();
  const [query, setQuery] = useState("");
  const [severity, setSeverity] = useState("all");
  const [wcag, setWcag] = useState("all");
  const [route, setRoute] = useState("all");
  const [resolution, setResolution] = useState("all");

  const routes = useMemo(
    () => [...new Set(run.issues.map((issue) => issue.route))].sort(),
    [run.issues],
  );
  const wcagReferences = useMemo(
    () => [...new Set(run.issues.flatMap((issue) => issue.wcag))].sort(),
    [run.issues],
  );
  const filteredIssues = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return run.issues
      .filter((issue) => severity === "all" || issue.severity === severity)
      .filter((issue) => wcag === "all" || issue.wcag.includes(wcag))
      .filter((issue) => route === "all" || issue.route === route)
      .filter(
        (issue) =>
          resolution === "all" ||
          issue.status === resolution,
      )
      .filter(
        (issue) =>
          !normalizedQuery ||
          [issue.title, issue.explanation, issue.selector, issue.route, issue.id, issue.status]
            .join(" ")
            .toLowerCase()
            .includes(normalizedQuery),
      )
      .sort(
        (left, right) =>
          severityOrder.indexOf(left.severity) - severityOrder.indexOf(right.severity),
      );
  }, [query, resolution, route, run.issues, severity, wcag]);

  const resetFilters = () => {
    setQuery("");
    setSeverity("all");
    setWcag("all");
    setRoute("all");
    setResolution("all");
  };
  const filtering = [query, severity, wcag, route, resolution].some(
    (value) => value && value !== "all",
  );

  return (
    <section id="scan-results" className="workspace-panel findings-panel" aria-labelledby="findings-title">
      <header className="panel-heading">
        <div>
          <p className="section-kicker"><span>Scan results</span></p>
          <h2 id="findings-title">Accessibility findings</h2>
          <p>Evidence from rendered routes, normalized into repairable issue records.</p>
        </div>
        <span className="count-badge">{run.issues.length} total</span>
      </header>

      <div className="filter-bar" aria-label="Filter scan results">
        <div className="filter-search">
          <label htmlFor={`${filterId}-search`}>Search findings</label>
          <input
            id={`${filterId}-search`}
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Title, selector, route…"
          />
        </div>
        <div>
          <label htmlFor={`${filterId}-severity`}>Severity</label>
          <select id={`${filterId}-severity`} value={severity} onChange={(event) => setSeverity(event.target.value)}>
            <option value="all">All severities</option>
            {severityOrder.map((item) => <option key={item} value={item}>{pretty(item)}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor={`${filterId}-wcag`}>WCAG</label>
          <select id={`${filterId}-wcag`} value={wcag} onChange={(event) => setWcag(event.target.value)}>
            <option value="all">All criteria</option>
            {wcagReferences.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor={`${filterId}-route`}>Route</label>
          <select id={`${filterId}-route`} value={route} onChange={(event) => setRoute(event.target.value)}>
            <option value="all">All routes</option>
            {routes.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor={`${filterId}-resolution`}>Status</label>
          <select id={`${filterId}-resolution`} value={resolution} onChange={(event) => setResolution(event.target.value)}>
            <option value="all">All findings</option>
            {issueStatuses.map((status) => (
              <option key={status} value={status}>{pretty(status)}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="filter-summary" aria-live="polite">
        <span>Showing {filteredIssues.length} of {run.issues.length} findings</span>
        {filtering ? <button type="button" onClick={resetFilters}>Clear filters</button> : null}
      </div>

      {filteredIssues.length > 0 ? (
        <div className="issue-list">
          {filteredIssues.map((issue) => <IssueCard key={issue.id} issue={issue} />)}
        </div>
      ) : (
        <div className="panel-empty">
          <span aria-hidden="true"><CheckIcon /></span>
          <h3>
            {!scanHasRun(run)
              ? "Waiting for scanner evidence"
              : run.issues.length === 0
                ? "No findings reported"
                : "No findings match these filters"}
          </h3>
          <p>
            {!scanHasRun(run)
              ? "Findings will appear as soon as the first rendered route is normalized."
              : run.issues.length === 0
                ? "The current scan returned no normalized accessibility issues."
                : "Broaden or clear the filters to inspect the remaining evidence."}
          </p>
        </div>
      )}
    </section>
  );
}

export function RepairPlanPanel({ run }: { run: RunView }) {
  return (
    <section id="repair-plan" className="workspace-panel repair-panel" aria-labelledby="repair-plan-title">
      <header className="panel-heading">
        <div>
          <p className="section-kicker"><span>Repair plan</span></p>
          <h2 id="repair-plan-title">Minimal, source-aware changes</h2>
          <p>Related findings are grouped to avoid duplicate or conflicting edits.</p>
        </div>
        <span className="count-badge">{run.repairPlan.length} groups</span>
      </header>

      {run.repairPlan.length > 0 ? (
        <ol className="repair-list">
          {run.repairPlan.map((item, index) => (
            <li key={item.id}>
              <article className="repair-card">
                <header>
                  <span className="repair-index">{String(index + 1).padStart(2, "0")}</span>
                  <div>
                    <h3>{item.title}</h3>
                    <p>{item.strategy}</p>
                  </div>
                  <span className={`risk-badge risk-${item.risk}`}>{pretty(item.risk)} risk</span>
                </header>

                <div className="repair-stats">
                  <span><strong>{item.issueIds.length}</strong> linked issues</span>
                  <span><strong>{item.files.length}</strong> likely files</span>
                  <span><strong>{percent(item.confidence)}</strong> confidence</span>
                  <span><strong>{pretty(item.status)}</strong> status</span>
                </div>

                {item.files.length > 0 ? (
                  <div className="file-list" aria-label="Files likely to change">
                    {item.files.map((file) => (
                      <code key={file}><FileCodeIcon />{file}</code>
                    ))}
                  </div>
                ) : (
                  <p className="muted-note">Source mapping is still resolving likely files.</p>
                )}
              </article>
            </li>
          ))}
        </ol>
      ) : (
        <div className="panel-empty compact-empty">
          <span aria-hidden="true"><SparkIcon /></span>
          <h3>Plan not available yet</h3>
          <p>
            Repair groups will appear after findings have been traced to source files.
          </p>
        </div>
      )}
    </section>
  );
}
