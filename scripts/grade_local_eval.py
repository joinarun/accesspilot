"""Grade AccessPilot traces with local deterministic metrics only."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from vertexai import types


def _load_evaluator(path: Path):
    spec = importlib.util.spec_from_file_location("accesspilot_eval_metrics", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.evaluate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--traces",
        type=Path,
        default=Path("artifacts/traces/local-traces.json"),
    )
    parser.add_argument(
        "--metric",
        type=Path,
        default=Path("tests/eval/metrics.py"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/grade_results/local-results.json"),
    )
    args = parser.parse_args()
    raw = json.loads(args.traces.read_text(encoding="utf-8"))
    dataset = types.EvaluationDataset.model_validate(raw)
    evaluate = _load_evaluator(args.metric)
    rows: list[dict] = []
    for case in dataset.eval_cases or []:
        response = case.responses[0].response.model_dump(mode="json")
        instance = {
            "prompt": case.prompt.model_dump(mode="json") if case.prompt else {},
            "response": response,
            "reference": case.reference.model_dump(mode="json")
            if case.reference
            else None,
            "agent_data": case.agent_data.model_dump(mode="json")
            if case.agent_data
            else {},
        }
        result = evaluate(instance)
        rows.append(
            {
                "eval_case_id": case.eval_case_id,
                "accesspilot_contract": result,
                "agent_turn_count": len(instance["agent_data"].get("turns", [])),
                "passed": float(result.get("score", 0)) >= 1.0,
            }
        )
    output = {
        "summary": {
            "cases": len(rows),
            "passed": sum(row["passed"] for row in rows),
            "failed": sum(not row["passed"] for row in rows),
        },
        "results": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print("case                              contract  turns  result")
    print("--------------------------------  --------  -----  ------")
    for row in rows:
        score = row["accesspilot_contract"]["score"]
        result = "PASS" if row["passed"] else "FAIL"
        print(
            f"{row['eval_case_id']:<32}  {score:>8.1f}  "
            f"{row['agent_turn_count']:>5}  {result}"
        )
    print(f"\n{output['summary']['passed']}/{output['summary']['cases']} cases passed")
    if output["summary"]["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
