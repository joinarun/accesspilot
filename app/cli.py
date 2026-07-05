"""Small installation helpers exposed by the Python distribution."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess

from app.services.safety import sanitized_environment
from app.settings import get_settings


def scanner_install_commands(
    *, with_deps: bool = False, skip_browser: bool = False
) -> list[list[str]]:
    commands = [["npm", "ci"]]
    if not skip_browser:
        browser = ["npx", "playwright", "install"]
        if with_deps:
            browser.append("--with-deps")
        browser.append("chromium")
        commands.append(browser)
    return commands


def install_scanner() -> None:
    """Install pinned Node dependencies and Chromium beside packaged scanner code."""
    parser = argparse.ArgumentParser(
        description="Install AccessPilot's pinned axe/Playwright scanner runtime."
    )
    parser.add_argument(
        "--with-deps",
        action="store_true",
        help="Also ask Playwright to install Linux system packages (often needs root).",
    )
    parser.add_argument(
        "--skip-browser",
        action="store_true",
        help="Install npm dependencies without downloading Chromium.",
    )
    args = parser.parse_args()

    node = shutil.which("node")
    npm = shutil.which("npm")
    npx = shutil.which("npx")
    if not node or not npm or (not args.skip_browser and not npx):
        raise SystemExit("Node.js 20+ with npm/npx is required.")
    version = subprocess.run(
        [node, "--version"],
        check=True,
        capture_output=True,
        text=True,
        env=sanitized_environment(),
    ).stdout.strip()
    match = re.fullmatch(r"v(\d+)(?:\.\d+){2}", version)
    if not match or int(match.group(1)) < 20:
        raise SystemExit(f"Node.js 20+ is required; found {version!r}.")

    scanner_root = get_settings().scanner_package_root
    if not scanner_root.is_dir() or not (scanner_root / "package-lock.json").is_file():
        raise SystemExit(f"Packaged scanner assets are missing from {scanner_root}.")
    if not os.access(scanner_root, os.W_OK):
        raise SystemExit(
            f"Scanner assets at {scanner_root} are not writable; install AccessPilot "
            "inside a user-owned virtual environment."
        )

    environment = sanitized_environment()
    for command in scanner_install_commands(
        with_deps=args.with_deps,
        skip_browser=args.skip_browser,
    ):
        subprocess.run(command, cwd=scanner_root, check=True, env=environment)
    print(f"AccessPilot scanner runtime installed in {scanner_root}.")


if __name__ == "__main__":
    install_scanner()
