"""Conservative repair planning and deterministic HTML/CSS patch application."""

from __future__ import annotations

import difflib
import hashlib
import html
import os
import re
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

from app.schemas import (
    AccessibilityIssue,
    FileChange,
    FixStatus,
    PatchProposal,
    RepairAction,
    RepairPlan,
    RiskLevel,
    SourceMapping,
)
from app.settings import AccessPilotSettings, get_settings

from .safety import sanitized_environment, validate_editable_path

_AUTOMATIC_RULES = {
    "html-has-lang",
    "document-title",
    "image-alt",
    "label",
    "label-title-only",
    "heading-order",
    "non-semantic-interactive",
    "focus-visible",
    "accessible-error-message",
    "color-contrast",
    "landmark-one-main",
    "region",
}
_CSS_RULES = {"focus-visible", "color-contrast"}
_MEDIUM_RISK_RULES = {"image-alt", "color-contrast", "non-semantic-interactive"}
_MARKUP_SUFFIXES = {".html", ".htm", ".jsx", ".tsx", ".vue", ".svelte"}
_COMPONENT_RULES = {
    "html-has-lang",
    "image-alt",
    "label",
    "label-title-only",
    "heading-order",
}


class RepairEngine:
    def __init__(self, settings: AccessPilotSettings | None = None) -> None:
        self.settings = settings or get_settings()

    def plan(
        self,
        root: Path,
        issues: list[AccessibilityIssue],
        mappings: list[SourceMapping],
    ) -> RepairPlan:
        mapping_by_issue = {mapping.issue_id: mapping for mapping in mappings}
        markup_files = sorted(
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in _MARKUP_SUFFIXES
        )
        css_files = sorted(path for path in root.rglob("*.css") if path.is_file())
        actions: list[RepairAction] = []
        unresolved: list[str] = []
        for issue in issues:
            mapping = mapping_by_issue.get(issue.id)
            selected = mapping.selected_path if mapping else None
            if issue.rule_id in _CSS_RULES and css_files:
                selected = css_files[0].relative_to(root).as_posix()
            elif (
                not selected
                and issue.rule_id in {"html-has-lang", "document-title"}
                and len(markup_files) == 1
            ):
                selected = markup_files[0].relative_to(root).as_posix()
            if issue.rule_id not in _AUTOMATIC_RULES or not selected:
                unresolved.append(issue.id)
                issue.status = FixStatus.UNRESOLVED
                continue
            suffix = Path(selected).suffix.lower()
            if issue.rule_id in _CSS_RULES and suffix not in {".css", ".scss"}:
                if not css_files:
                    unresolved.append(issue.id)
                    issue.status = FixStatus.UNRESOLVED
                    continue
                selected = css_files[0].relative_to(root).as_posix()
                suffix = Path(selected).suffix.lower()
            kind = "css" if suffix in {".css", ".scss"} else "html"
            if suffix not in _MARKUP_SUFFIXES | {".css", ".scss"}:
                unresolved.append(issue.id)
                issue.status = FixStatus.UNRESOLVED
                continue
            if suffix in {".jsx", ".tsx"} and issue.rule_id not in _COMPONENT_RULES:
                unresolved.append(issue.id)
                issue.status = FixStatus.UNRESOLVED
                continue
            if suffix == ".jsx":
                kind = "jsx"
            elif suffix == ".tsx":
                kind = "tsx"
            risk = (
                RiskLevel.MEDIUM
                if issue.rule_id in _MEDIUM_RISK_RULES
                else RiskLevel.LOW
            )
            actions.append(
                RepairAction(
                    id=f"repair-{issue.id}",
                    issue_ids=[issue.id],
                    file_path=selected,
                    strategy=issue.suggested_fix,
                    rationale=f"Deterministic {issue.rule_id} repair backed by scanner evidence.",
                    change_kind=kind,
                    risk=risk,
                    confidence=min(
                        issue.confidence, mapping.confidence if mapping else 0.75
                    ),
                    expected_result=f"Remove the {issue.rule_id} finding on {issue.route}.",
                    automatic=True,
                )
            )
            issue.status = FixStatus.PLANNED
        overall = (
            RiskLevel.MEDIUM
            if any(action.risk == RiskLevel.MEDIUM for action in actions)
            else RiskLevel.LOW
        )
        return RepairPlan(
            summary=(
                f"Apply {len(actions)} bounded repair(s); "
                f"leave {len(unresolved)} issue(s) for human review."
            ),
            actions=actions,
            unresolved_issue_ids=unresolved,
            overall_risk=overall,
            generated_by="deterministic-repair-planner",
        )

    def apply(
        self,
        root: Path,
        plan: RepairPlan,
        issues: list[AccessibilityIssue],
        proposals: list[PatchProposal] | None = None,
    ) -> list[FileChange]:
        issue_by_id = {issue.id: issue for issue in issues}
        approved_actions_by_file: dict[str, list[RepairAction]] = defaultdict(list)
        actions_by_file: dict[str, list[RepairAction]] = defaultdict(list)
        for action in plan.actions:
            approved_actions_by_file[action.file_path].append(action)
            if action.automatic:
                actions_by_file[action.file_path].append(action)
        approved_paths = set(approved_actions_by_file)
        proposal_issue_ids: dict[str, set[str]] = defaultdict(set)
        proposal_summaries: dict[str, set[str]] = defaultdict(set)
        originals: dict[str, str] = {}
        for relative in approved_paths:
            try:
                path = validate_editable_path(root, relative)
                if path.stat().st_size <= self.settings.max_source_file_bytes:
                    originals[relative] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        for proposal in proposals or []:
            if proposal.path not in approved_paths or proposal.path not in originals:
                continue
            allowed_issue_ids = {
                issue_id
                for action in approved_actions_by_file[proposal.path]
                for issue_id in action.issue_ids
            }
            if (
                not proposal.issue_ids
                or not set(proposal.issue_ids) <= allowed_issue_ids
            ):
                continue
            if _apply_unified_diff(root, proposal):
                proposal_issue_ids[proposal.path].update(proposal.issue_ids)
                proposal_summaries[proposal.path].add(proposal.change_summary)
        for relative, actions in actions_by_file.items():
            path = validate_editable_path(root, relative)
            if path.stat().st_size > self.settings.max_source_file_bytes:
                continue
            before = path.read_text(encoding="utf-8")
            rules = {
                issue_by_id[issue_id].rule_id
                for action in actions
                for issue_id in action.issue_ids
                if issue_id in issue_by_id
            }
            if path.suffix.lower() in {".html", ".htm"}:
                after = _repair_html(before, rules)
            elif path.suffix.lower() in {".jsx", ".tsx"}:
                after = _repair_component(before, rules, jsx=True)
            elif path.suffix.lower() in {".vue", ".svelte"}:
                after = _repair_component(before, rules, jsx=False)
            elif path.suffix.lower() in {".css", ".scss"}:
                after = _repair_css(before, rules)
            else:
                continue
            if after != before:
                _atomic_write(path, after)

        changes: list[FileChange] = []
        for relative, original in originals.items():
            path = validate_editable_path(root, relative)
            try:
                final = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if final == original:
                continue
            diff = "".join(
                difflib.unified_diff(
                    original.splitlines(keepends=True),
                    final.splitlines(keepends=True),
                    fromfile=f"a/{relative}",
                    tofile=f"b/{relative}",
                )
            )
            additions = sum(
                1
                for line in diff.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            )
            deletions = sum(
                1
                for line in diff.splitlines()
                if line.startswith("-") and not line.startswith("---")
            )
            issue_ids = sorted(
                {
                    item
                    for action in approved_actions_by_file[relative]
                    for item in action.issue_ids
                }
                | proposal_issue_ids[relative]
            )
            rules = {
                issue_by_id[issue_id].rule_id
                for issue_id in issue_ids
                if issue_id in issue_by_id
            }
            summary_parts = set(rules) | proposal_summaries[relative]
            changes.append(
                FileChange(
                    path=relative,
                    issue_ids=issue_ids,
                    summary="; ".join(sorted(summary_parts)),
                    before_sha256=_sha256(original),
                    after_sha256=_sha256(final),
                    additions=additions,
                    deletions=deletions,
                    diff=diff,
                )
            )
        return changes


def _apply_unified_diff(root: Path, proposal: PatchProposal) -> bool:
    diff = proposal.unified_diff
    if not diff or len(diff) > 200_000 or "\x00" in diff:
        return False
    lowered = diff.lower()
    if any(
        marker in lowered
        for marker in (
            "git binary patch",
            "new file mode",
            "deleted file mode",
            "rename from",
            "rename to",
            "copy from",
            "copy to",
            "submodule",
        )
    ):
        return False
    header_paths: list[str] = []
    for line in diff.splitlines():
        if not line.startswith(("--- ", "+++ ")):
            continue
        raw = line[4:].split("\t", 1)[0].strip()
        if raw == "/dev/null":
            return False
        if raw.startswith(("a/", "b/")):
            raw = raw[2:]
        header_paths.append(raw)
    if len(header_paths) < 2 or set(header_paths) != {proposal.path}:
        return False
    try:
        validate_editable_path(root, proposal.path)
    except Exception:
        return False
    env = sanitized_environment({"GIT_CONFIG_NOSYSTEM": "1", "GIT_OPTIONAL_LOCKS": "0"})
    try:
        check = subprocess.run(
            ["git", "apply", "--check", "--recount", "-"],
            cwd=root,
            env=env,
            input=diff,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if check.returncode != 0:
        return False
    try:
        applied = subprocess.run(
            ["git", "apply", "--recount", "-"],
            cwd=root,
            env=env,
            input=diff,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return applied.returncode == 0


def changes_fingerprint(changes: list[FileChange]) -> str:
    payload = "\n".join(
        f"{change.path}:{change.after_sha256}"
        for change in sorted(changes, key=lambda item: item.path)
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _repair_html(source: str, rules: set[str]) -> str:
    value = source
    if "html-has-lang" in rules:
        value = re.sub(
            r"<html(?![^>]*\blang=)([^>]*)>",
            r'<html lang="en"\1>',
            value,
            count=1,
            flags=re.I,
        )
    if "document-title" in rules and not re.search(r"<title\b", value, re.I):
        heading = re.search(r"<h1[^>]*>(.*?)</h1>", value, re.I | re.S)
        title = (
            _strip_tags(heading.group(1)) if heading else "Accessible web application"
        )
        value = re.sub(
            r"</head>",
            f"  <title>{html.escape(title)}</title>\n  </head>",
            value,
            count=1,
            flags=re.I,
        )
    if "image-alt" in rules:
        value = re.sub(r"<img\b[^>]*>", _add_image_alt, value, flags=re.I)
    if rules & {"label", "label-title-only"}:
        value = _add_labels(value)
    if "heading-order" in rules:
        value = _repair_heading_order(value)
    if "non-semantic-interactive" in rules:
        value = _replace_clickable_generics(value)
    if "accessible-error-message" in rules:
        value = _repair_error_messages(value)
    if rules & {"landmark-one-main", "region"}:
        value = _repair_landmarks(value)
    return value


def _repair_component(source: str, rules: set[str], *, jsx: bool) -> str:
    def repair_markup(value: str) -> str:
        if "html-has-lang" in rules:
            value = re.sub(
                r"<html(?![^>]*\blang=)([^>]*)>",
                r'<html lang="en"\1>',
                value,
                count=1,
                flags=re.I,
            )
        if "image-alt" in rules:
            value = re.sub(r"<img\b[^>]*?/?>", _add_image_alt, value, flags=re.I)
        if rules & {"label", "label-title-only"}:
            value = _add_labels(value, jsx=jsx)
        if "heading-order" in rules:
            value = _repair_heading_order(value)
        if not jsx:
            if "non-semantic-interactive" in rules:
                value = _replace_clickable_generics(value)
            if "accessible-error-message" in rules:
                value = _repair_error_messages(value)
            if rules & {"landmark-one-main", "region"}:
                value = _repair_landmarks(value)
        return value

    if not jsx and "<template" in source.lower():
        return re.sub(
            r"(<template\b[^>]*>)(.*?)(</template>)",
            lambda match: (
                f"{match.group(1)}{repair_markup(match.group(2))}{match.group(3)}"
            ),
            source,
            count=1,
            flags=re.I | re.S,
        )
    return repair_markup(source)


def _repair_css(source: str, rules: set[str]) -> str:
    value = source
    if "focus-visible" in rules:
        value = re.sub(
            r"outline\s*:\s*none\s*;?",
            "outline: 3px solid #2563eb;\n  outline-offset: 3px;",
            value,
            flags=re.I,
        )
    if "color-contrast" in rules:
        replacements = {
            "#b8bec8": "#475467",
            "#8f96a3": "#475467",
            "#a0a7b2": "#475467",
        }
        for before, after in replacements.items():
            value = re.sub(re.escape(before), after, value, flags=re.I)
    return value


def _add_image_alt(match: re.Match[str]) -> str:
    tag = match.group(0)
    if re.search(r"\balt\s*=", tag, re.I):
        return tag
    src_match = re.search(r"\bsrc\s*=\s*([\"'])(.*?)\1", tag, re.I)
    if not src_match:
        return tag
    stem = Path(src_match.group(2).split("?")[0]).stem
    if stem.lower() in {"spacer", "divider", "background", "decoration"}:
        alt = ""
    else:
        alt = re.sub(r"[-_]+", " ", stem).strip().capitalize()
    ending = " />" if tag.rstrip().endswith("/>") else ">"
    trim = 2 if ending == " />" else 1
    return tag[:-trim].rstrip() + f' alt="{html.escape(alt, quote=True)}"{ending}'


def _add_labels(source: str, *, jsx: bool = False) -> str:
    for_attribute = "htmlFor" if jsx else "for"
    class_attribute = "className" if jsx else "class"
    labelled_ids = set(
        re.findall(
            r"<label\b[^>]*\b(?:for|htmlFor)\s*=\s*[\"']([^\"']+)",
            source,
            re.I,
        )
    )

    def replacer(match: re.Match[str]) -> str:
        tag = match.group(0)
        input_type = _attribute(tag, "type").lower()
        if input_type in {"hidden", "submit", "button", "reset", "image"}:
            return tag
        control_id = _attribute(tag, "id")
        if (
            not control_id
            or control_id in labelled_ids
            or re.search(r"\baria-label(?:ledby)?\s*=", tag, re.I)
        ):
            return tag
        label = _attribute(tag, "placeholder") or _attribute(tag, "name") or control_id
        label = re.sub(r"[-_]+", " ", label).strip().capitalize()
        labelled_ids.add(control_id)
        return (
            f'<label {class_attribute}="accesspilot-label" {for_attribute}="{html.escape(control_id, quote=True)}">'
            f"{html.escape(label)}</label>\n            {tag}"
        )

    return re.sub(r"<(?:input|select|textarea)\b[^>]*>", replacer, source, flags=re.I)


def _repair_heading_order(source: str) -> str:
    previous = 0

    def replacer(match: re.Match[str]) -> str:
        nonlocal previous
        level = int(match.group(1))
        new_level = level
        if previous and level > previous + 1:
            new_level = previous + 1
        previous = new_level
        return f"<h{new_level}{match.group(2)}>{match.group(3)}</h{new_level}>"

    return re.sub(r"<h([1-6])([^>]*)>(.*?)</h\1>", replacer, source, flags=re.I | re.S)


def _replace_clickable_generics(source: str) -> str:
    pattern = re.compile(
        r"<(div|span)([^>]*(?:onclick\s*=|\bid\s*=\s*[\"'][^\"']*(?:action|button|trigger)[^\"']*[\"'])[^>]*)>"
        r"(.*?)</\1>",
        re.I | re.S,
    )

    def replacer(match: re.Match[str]) -> str:
        attributes = re.sub(
            r"\s+(?:role|tabindex)\s*=\s*[\"'][^\"']*[\"']",
            "",
            match.group(2),
            flags=re.I,
        )
        return f'<button type="button"{attributes}>{match.group(3)}</button>'

    return pattern.sub(replacer, source)


def _repair_error_messages(source: str) -> str:
    error_ids: list[str] = []

    def error_replacer(match: re.Match[str]) -> str:
        tag = match.group(0)
        error_id = _attribute(tag, "id")
        if error_id:
            error_ids.append(error_id)
        if not re.search(r"\brole\s*=", tag, re.I):
            tag = tag[:-1].rstrip() + ' role="alert">'
        if not re.search(r"\baria-live\s*=", tag, re.I):
            tag = tag[:-1].rstrip() + ' aria-live="polite">'
        return tag

    value = re.sub(
        r"<[^>]*class\s*=\s*[\"'][^\"']*(?:error|error-message)[^\"']*[\"'][^>]*>",
        error_replacer,
        source,
        flags=re.I,
    )
    if error_ids:
        error_id = error_ids[0]

        def input_replacer(match: re.Match[str]) -> str:
            tag = match.group(0)
            if re.search(r"\baria-describedby\s*=", tag, re.I):
                return tag
            return (
                tag[:-1].rstrip()
                + f' aria-describedby="{html.escape(error_id, quote=True)}">'
            )

        value = re.sub(r"<input\b[^>]*>", input_replacer, value, count=1, flags=re.I)
    return value


def _repair_landmarks(source: str) -> str:
    value = re.sub(
        r"<div(\s+class\s*=\s*[\"'][^\"']*\bnav\b[^\"']*[\"'][^>]*)>(.*?)</div>",
        r'<nav aria-label="Primary"\1>\2</nav>',
        source,
        count=1,
        flags=re.I | re.S,
    )
    value = re.sub(
        r"<div(\s+class\s*=\s*[\"'][^\"']*site-header[^\"']*[\"'][^>]*)>(.*?)</div>",
        r"<header\1>\2</header>",
        value,
        count=1,
        flags=re.I | re.S,
    )
    shell = re.search(
        r"<div(\s+class\s*=\s*[\"'][^\"']*page-shell[^\"']*[\"'][^>]*)>(.*)</div>(\s*<div\s+class\s*=\s*[\"'][^\"']*toast)",
        value,
        re.I | re.S,
    )
    if shell:
        value = (
            value[: shell.start()]
            + f"<main{shell.group(1)}>{shell.group(2)}</main>{shell.group(3)}"
            + value[shell.end() :]
        )
    value = re.sub(
        r"<div(\s+class\s*=\s*[\"'][^\"']*toast[^\"']*[\"'][^>]*)>",
        r'<div role="status" aria-live="polite"\1>',
        value,
        count=1,
        flags=re.I,
    )
    return value


def _attribute(tag: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}\s*=\s*([\"'])(.*?)\1", tag, re.I | re.S)
    return html.unescape(match.group(2)).strip() if match else ""


def _strip_tags(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _atomic_write(path: Path, value: str) -> None:
    mode = path.stat().st_mode
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


repair_engine = RepairEngine()
