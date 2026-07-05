"""Generate canonical eval traces without the Vertex/ADC-only CLI loader."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from pathlib import Path

from google.adk.apps import App, ResumabilityConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

# The bundled local eval intentionally scans the loopback demo/UI server. This
# opts in only for this short-lived evaluator process; the API default remains
# private-network deny.
os.environ.setdefault("ACCESSPILOT_ALLOW_PRIVATE_NETWORK_TARGETS", "true")

from app.workflow import build_accesspilot_workflow


async def generate(dataset_path: Path, output_path: Path) -> None:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    populated: list[dict] = []
    for case in dataset.get("eval_cases", []):
        prompt = case.get("prompt")
        if not prompt:
            raise ValueError(f"{case.get('eval_case_id')} has no prompt")
        session_service = InMemorySessionService()
        session_id = f"eval-{uuid.uuid4().hex}"
        await session_service.create_session(
            app_name="app", user_id="eval-user", session_id=session_id
        )
        workflow = build_accesspilot_workflow(deterministic_specialists=True)
        runner = Runner(
            app=App(
                name="app",
                root_agent=workflow,
                resumability_config=ResumabilityConfig(is_resumable=True),
            ),
            session_service=session_service,
        )
        content = types.Content.model_validate(prompt)
        response_text = ""
        async for event in runner.run_async(
            user_id="eval-user",
            session_id=session_id,
            new_message=content,
        ):
            if event.content and event.content.parts:
                texts = [part.text for part in event.content.parts if part.text]
                if texts:
                    response_text = "".join(texts)
        if not response_text:
            raise RuntimeError(
                f"{case.get('eval_case_id')} produced no final text response"
            )
        populated_case = dict(case)
        populated_case["responses"] = [
            {
                "response": {
                    "role": "model",
                    "parts": [{"text": response_text}],
                }
            }
        ]
        populated_case["agent_data"] = {
            "turns": [
                {
                    "turn_index": 0,
                    "events": [
                        {"author": "user", "content": prompt},
                        {
                            "author": "accesspilot_workflow",
                            "content": {
                                "role": "model",
                                "parts": [{"text": response_text}],
                            },
                        },
                    ],
                }
            ]
        }
        populated.append(populated_case)
        print(f"generated {case.get('eval_case_id')}: {response_text}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps({"eval_cases": populated}, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("tests/eval/datasets/basic-dataset.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/traces/local-traces.json"),
    )
    args = parser.parse_args()
    asyncio.run(generate(args.dataset, args.output))


if __name__ == "__main__":
    main()
