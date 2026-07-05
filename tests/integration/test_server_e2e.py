import os
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    if not (ROOT / "scanner" / "node_modules" / "@axe-core" / "playwright").exists():
        pytest.skip("scanner dependencies are not installed")
    port = _port()
    url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update(
        {
            "ACCESSPILOT_DETERMINISTIC_SPECIALISTS": "true",
            "ACCESSPILOT_PUBLIC_BASE_URL": url,
        }
    )
    process = subprocess.Popen(
        [
            str(ROOT / ".venv" / "bin" / "python"),
            "-m",
            "uvicorn",
            "app.fast_api_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/api/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    else:
        process.terminate()
        raise RuntimeError("AccessPilot test server did not start")
    yield url
    process.terminate()
    process.wait(timeout=10)


def _wait_for(url: str, run_id: str, statuses: set[str], timeout: int = 90) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = httpx.get(f"{url}/api/runs/{run_id}", timeout=5).json()
        if payload["state"]["status"] in statuses:
            return payload
        time.sleep(0.25)
    raise AssertionError(f"run {run_id} did not reach {statuses}")


def test_sample_scan_repair_verify_and_reject_gate(server: str) -> None:
    response = httpx.post(
        f"{server}/api/runs",
        json={
            "target": "sample://vulnerable-app",
            "demo_mode": True,
            "routes": ["/"],
            "max_repair_rounds": 3,
        },
        timeout=10,
    )
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    review = _wait_for(server, run_id, {"waiting_for_approval", "failed"})
    state = review["state"]
    assert state["error"] is None
    assert state["verification"]["passed"] is True
    assert state["verification"]["baseline_issue_count"] > 0
    assert state["verification"]["fixed_issue_count"] == len(state["issues"])
    assert state["verification"]["remaining_issue_count"] == 0
    assert state["verification"]["current_issue_count"] == 0
    assert len(state["pages"]) == 1
    assert {change["path"] for change in state["changes"]} == {
        "index.html",
        "styles.css",
    }
    assert state["pull_request_url"] is None

    rejected = httpx.post(
        f"{server}/api/runs/{run_id}/approval",
        json={
            "decision": "reject",
            "expected_diff_sha256": state["verification"]["diff_sha256"],
            "reviewer_note": "Integration safety check",
        },
        timeout=10,
    )
    assert rejected.status_code == 202
    final = _wait_for(server, run_id, {"rejected", "failed"})["state"]
    assert final["status"] == "rejected"
    assert final["approval"]["decision"] == "reject"
    assert final["pull_request_url"] is None


def test_remote_mutation_requires_server_side_opt_in(server: str) -> None:
    response = httpx.post(
        f"{server}/api/runs",
        json={
            "target": "sample://vulnerable-app",
            "demo_mode": True,
            "execute_pull_request": True,
        },
        timeout=10,
    )

    assert response.status_code == 403
    assert "disabled by server policy" in response.json()["detail"]


def test_runtime_capabilities_are_reported_and_server_gated(server: str) -> None:
    config = httpx.get(f"{server}/api/config", timeout=10).json()
    assert config["local_execution_enabled"] is False
    assert config["dependency_install_enabled"] is False

    local_execution = httpx.post(
        f"{server}/api/runs",
        json={
            "target": "sample://vulnerable-app",
            "allow_local_execution": True,
        },
        timeout=10,
    )
    dependency_install = httpx.post(
        f"{server}/api/runs",
        json={
            "target": "sample://vulnerable-app",
            "allow_dependency_install": True,
        },
        timeout=10,
    )

    assert local_execution.status_code == 403
    assert dependency_install.status_code == 403


def test_revision_cannot_bypass_repair_round_limit(server: str) -> None:
    response = httpx.post(
        f"{server}/api/runs",
        json={
            "target": "sample://vulnerable-app",
            "demo_mode": True,
            "routes": ["/"],
            "max_repair_rounds": 1,
        },
        timeout=10,
    )
    run_id = response.json()["run_id"]
    review = _wait_for(server, run_id, {"waiting_for_approval", "failed"})["state"]
    revised = httpx.post(
        f"{server}/api/runs/{run_id}/approval",
        json={
            "decision": "revise",
            "expected_diff_sha256": review["verification"]["diff_sha256"],
            "reviewer_note": "Try a different semantic repair.",
        },
        timeout=10,
    )
    assert revised.status_code == 202

    final = _wait_for(server, run_id, {"stopped", "failed"})["state"]
    assert final["status"] == "stopped"
    assert final["pull_request_url"] is None
    assert any("round limit" in item["text"] for item in final["messages"])
