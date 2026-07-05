import { useEffect, useId, useState, type FormEvent } from "react";

import type { AppConfig, RunRequest } from "../types";
import { ArrowRightIcon, ShieldCheckIcon } from "./Icons";

interface IntakeFormProps {
  config: AppConfig;
  disabled?: boolean;
  submitting?: boolean;
  onSubmit: (request: RunRequest) => Promise<void>;
}

interface FormState {
  target: string;
  branch: string;
  provider: string;
  wcagLevel: string;
  routes: string;
  scanDepth: number;
  authenticationInstructions: string;
  appUrl: string;
  allowLocalExecution: boolean;
  allowDependencyInstall: boolean;
  executePullRequest: boolean;
}

export function IntakeForm({
  config,
  disabled = false,
  submitting = false,
  onSubmit,
}: IntakeFormProps) {
  const formId = useId();
  const [form, setForm] = useState<FormState>({
    target: "",
    branch: "accesspilot/a11y-repairs",
    provider: config.defaultProvider,
    wcagLevel: config.defaultWcagLevel,
    routes: "/",
    scanDepth: config.defaultScanDepth,
    authenticationInstructions: "",
    appUrl: "",
    allowLocalExecution: false,
    allowDependencyInstall: false,
    executePullRequest: false,
  });
  const [error, setError] = useState("");

  useEffect(() => {
    setForm((current) => ({
      ...current,
      provider: current.provider || config.defaultProvider,
      wcagLevel: current.wcagLevel || config.defaultWcagLevel,
      scanDepth: Number.isFinite(current.scanDepth)
        ? current.scanDepth
        : config.defaultScanDepth,
      allowLocalExecution: config.localExecutionEnabled
        ? current.allowLocalExecution
        : false,
      allowDependencyInstall:
        config.localExecutionEnabled && config.dependencyInstallEnabled
          ? current.allowDependencyInstall
          : false,
      executePullRequest: config.remoteMutationsEnabled
        ? current.executePullRequest
        : false,
    }));
  }, [config]);

  const update = <Key extends keyof FormState>(key: Key, value: FormState[Key]) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const target = form.target.trim();

    if (!target) {
      setError("Enter a website URL, GitHub repository, or local project path.");
      document.getElementById(`${formId}-target`)?.focus();
      return;
    }

    const routes = form.routes
      .split(/[\n,]/)
      .map((route) => route.trim())
      .filter(Boolean);

    setError("");
    await onSubmit({
      target,
      branch_name: form.branch.trim() || undefined,
      wcag_level: form.wcagLevel,
      routes,
      scan_depth: form.scanDepth,
      llm_provider: form.provider,
      authentication_instructions:
        form.authenticationInstructions.trim() || undefined,
      app_url: form.appUrl.trim() || undefined,
      allow_local_execution:
        config.localExecutionEnabled && form.allowLocalExecution,
      allow_dependency_install:
        config.localExecutionEnabled &&
        config.dependencyInstallEnabled &&
        form.allowLocalExecution &&
        form.allowDependencyInstall,
      execute_pull_request:
        config.remoteMutationsEnabled && form.executePullRequest,
    });
  };

  return (
    <section className="intake-card" aria-labelledby={`${formId}-title`}>
      <div className="section-kicker">
        <span>New assessment</span>
        <span className="safe-chip">
          <ShieldCheckIcon /> {form.executePullRequest ? "Remote publication requested" : "Local changes only"}
        </span>
      </div>
      <h2 id={`${formId}-title`}>Point AccessPilot at your application</h2>
      <p className="section-intro">
        Start with a URL or source repository. Remote publication requires both an
        explicit request here and approval of the exact verified diff.
      </p>

      <form onSubmit={submit} noValidate>
        <div className="field field-wide">
          <label htmlFor={`${formId}-target`}>Website or repository</label>
          <input
            id={`${formId}-target`}
            name="target"
            type="text"
            autoComplete="url"
            placeholder="https://example.com, GitHub URL, or /local/project"
            value={form.target}
            onChange={(event) => update("target", event.target.value)}
            aria-describedby={`${formId}-target-hint${error ? ` ${formId}-error` : ""}`}
            aria-invalid={Boolean(error)}
            disabled={disabled}
          />
          <span id={`${formId}-target-hint`} className="field-hint">
            AccessPilot detects URL, repository, and local path targets automatically.
          </span>
          <button
            className="sample-target-button"
            type="button"
            onClick={() =>
              setForm((current) => ({
                ...current,
                target: config.sampleTarget,
                appUrl: "",
                allowLocalExecution: false,
                allowDependencyInstall: false,
              }))
            }
            disabled={disabled}
          >
            Use bundled vulnerable app
          </button>
        </div>

        <div className="form-grid">
          <div className="field">
            <label htmlFor={`${formId}-provider`}>Reasoning provider</label>
            <div className="select-wrap">
              <select
                id={`${formId}-provider`}
                value={form.provider}
                onChange={(event) => update("provider", event.target.value)}
                disabled={disabled}
              >
                {config.providers.map((provider) => (
                  <option key={provider.value} value={provider.value}>
                    {provider.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="field">
            <label htmlFor={`${formId}-wcag`}>Target conformance</label>
            <div className="select-wrap">
              <select
                id={`${formId}-wcag`}
                value={form.wcagLevel}
                onChange={(event) => update("wcagLevel", event.target.value)}
                disabled={disabled}
              >
                {config.wcagLevels.map((level) => (
                  <option key={level.value} value={level.value}>
                    {level.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="field">
            <label htmlFor={`${formId}-branch`}>Working branch</label>
            <input
              id={`${formId}-branch`}
              type="text"
              value={form.branch}
              onChange={(event) => update("branch", event.target.value)}
              placeholder="accesspilot/a11y-repairs"
              disabled={disabled}
            />
          </div>

          <div className="field">
            <label htmlFor={`${formId}-depth`}>Scan depth</label>
            <input
              id={`${formId}-depth`}
              type="number"
              min={0}
              max={config.maxScanDepth}
              value={form.scanDepth}
              onChange={(event) =>
                update(
                  "scanDepth",
                  Math.max(0, Math.min(config.maxScanDepth, Number(event.target.value))),
                )
              }
              disabled={disabled}
              aria-describedby={`${formId}-depth-hint`}
            />
            <span id={`${formId}-depth-hint`} className="field-hint">
              Maximum same-origin navigation levels.
            </span>
          </div>
        </div>

        <div className="field field-wide">
          <label htmlFor={`${formId}-routes`}>Routes to scan</label>
          <textarea
            id={`${formId}-routes`}
            rows={3}
            value={form.routes}
            onChange={(event) => update("routes", event.target.value)}
            placeholder={'/\n/account\n/checkout'}
            disabled={disabled}
            aria-describedby={`${formId}-routes-hint`}
          />
          <span id={`${formId}-routes-hint`} className="field-hint">
            One path per line or comma separated. Network crawling is limited to
            the bundled demo and AccessPilot-managed local apps.
          </span>
        </div>

        <details className="advanced-fields">
          <summary>Runtime and authentication options <span>Optional</span></summary>
          <div className="advanced-grid">
            <div className="field advanced-wide">
              <label htmlFor={`${formId}-app-url`}>Running application URL</label>
              <input
                id={`${formId}-app-url`}
                type="url"
                value={form.appUrl}
                onChange={(event) => update("appUrl", event.target.value)}
                placeholder="https://staging.example.com"
                disabled={disabled}
                aria-describedby={`${formId}-app-url-hint`}
              />
              <span id={`${formId}-app-url-hint`} className="field-hint">
                For a repository target, scan this already-running public or
                operator-approved URL instead of starting the source locally.
              </span>
            </div>

            <div
              className={`runtime-option${config.localExecutionEnabled ? "" : " is-disabled"}`}
            >
              <label htmlFor={`${formId}-local-execution`}>
                <input
                  id={`${formId}-local-execution`}
                  type="checkbox"
                  checked={form.allowLocalExecution}
                  onChange={(event) => {
                    update("allowLocalExecution", event.target.checked);
                    if (!event.target.checked) update("allowDependencyInstall", false);
                  }}
                  disabled={disabled || !config.localExecutionEnabled}
                />
                <span>
                  <strong>Start repository locally</strong>
                  Run the detected allow-listed start command in the isolated copy.
                </span>
              </label>
              <p>
                {config.localExecutionEnabled
                  ? "Explicit per-run opt-in; host execution is not an OS sandbox."
                  : "Disabled by ACCESSPILOT_ENABLE_LOCAL_EXECUTION=false on the API server."}
              </p>
            </div>

            <div
              className={`runtime-option${config.dependencyInstallEnabled ? "" : " is-disabled"}`}
            >
              <label htmlFor={`${formId}-dependency-install`}>
                <input
                  id={`${formId}-dependency-install`}
                  type="checkbox"
                  checked={form.allowDependencyInstall}
                  onChange={(event) =>
                    update("allowDependencyInstall", event.target.checked)
                  }
                  disabled={
                    disabled ||
                    !config.localExecutionEnabled ||
                    !config.dependencyInstallEnabled ||
                    !form.allowLocalExecution
                  }
                />
                <span>
                  <strong>Install missing dependencies</strong>
                  Permit the detected package-manager install step for this run.
                </span>
              </label>
              <p>
                {config.dependencyInstallEnabled
                  ? "Available only together with local execution."
                  : "Disabled by ACCESSPILOT_ENABLE_DEPENDENCY_INSTALL=false on the API server."}
              </p>
            </div>
          </div>
          <div className="field">
            <label htmlFor={`${formId}-auth`}>Non-secret sign-in guidance</label>
            <textarea
              id={`${formId}-auth`}
              rows={4}
              value={form.authenticationInstructions}
              onChange={(event) =>
                update("authenticationInstructions", event.target.value)
              }
              placeholder="Example: open /login and use the test-account environment variables"
              disabled={disabled}
              aria-describedby={`${formId}-auth-hint`}
            />
            <span id={`${formId}-auth-hint`} className="field-hint warning-hint">
              Do not paste passwords, API keys, session cookies, or access tokens here.
            </span>
          </div>
        </details>

        <div className={`remote-publication${config.remoteMutationsEnabled ? "" : " is-disabled"}`}>
          <label htmlFor={`${formId}-remote-publication`}>
            <input
              id={`${formId}-remote-publication`}
              type="checkbox"
              checked={form.executePullRequest}
              onChange={(event) => update("executePullRequest", event.target.checked)}
              disabled={disabled || !config.remoteMutationsEnabled}
              aria-describedby={`${formId}-remote-publication-hint`}
            />
            <span>
              <strong>Allow remote publication after approval</strong>
              Push the reviewed branch and create a pull request only after I approve it.
            </span>
          </label>
          <p id={`${formId}-remote-publication-hint`}>
            {config.remoteMutationsEnabled
              ? "Unchecked by default. Leave this off to run the full workflow without pushing or creating a pull request."
              : "The API server reports remote_mutations_enabled=false. Set ACCESSPILOT_ENABLE_REMOTE_MUTATIONS=true on the API server and reload this page to allow publishing."}
          </p>
        </div>

        {error ? (
          <p id={`${formId}-error`} className="form-error" role="alert">
            {error}
          </p>
        ) : null}

        <div className="form-actions">
          <p>
            Scanner evidence is deterministic. AI is used to investigate and plan
            repairs.
          </p>
          <button className="button button-primary" type="submit" disabled={disabled}>
            {submitting ? (
              <>
                <span className="spinner" aria-hidden="true" /> Starting scan…
              </>
            ) : (
              <>
                Start assessment <ArrowRightIcon />
              </>
            )}
          </button>
        </div>
      </form>
    </section>
  );
}
