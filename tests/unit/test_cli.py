from app.cli import scanner_install_commands


def test_scanner_bootstrap_commands_are_argv_only() -> None:
    assert scanner_install_commands() == [
        ["npm", "ci"],
        ["npx", "playwright", "install", "chromium"],
    ]
    assert scanner_install_commands(with_deps=True) == [
        ["npm", "ci"],
        ["npx", "playwright", "install", "--with-deps", "chromium"],
    ]
    assert scanner_install_commands(skip_browser=True) == [["npm", "ci"]]
