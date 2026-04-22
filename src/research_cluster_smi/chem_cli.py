"""Computational chemistry helpers for SMI task specs and cluster manifests."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROGRAM_DEFAULTS = {
    "qe": {
        "pattern": "*.in",
        "output_suffix": ".out",
        "template": "examples/slurm/qe-manifest-worker.sbatch",
        "program_name": "Quantum ESPRESSO",
    },
    "orca": {
        "pattern": "*.inp",
        "output_suffix": ".out",
        "template": "examples/slurm/orca-manifest-worker.sbatch",
        "program_name": "ORCA",
    },
}


def discover_inputs(input_dir: Path, pattern: str) -> list[Path]:
    return sorted(path for path in input_dir.glob(pattern) if path.is_file())


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    if str(path) == "-":
        print(json.dumps(payload, indent=2))
        return
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def make_manifest(args: argparse.Namespace) -> int:
    input_dir = Path(args.input_dir)
    pattern = args.pattern or PROGRAM_DEFAULTS[args.program]["pattern"]
    inputs = discover_inputs(input_dir, pattern)
    if not inputs:
        print(f"No inputs found in {input_dir} matching {pattern}", file=sys.stderr)
        return 1
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [str(path.relative_to(input_dir)) for path in inputs]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(lines)} entries to {output}")
    return 0


def make_spec(args: argparse.Namespace) -> int:
    input_dir = Path(args.input_dir)
    program = PROGRAM_DEFAULTS[args.program]
    pattern = args.pattern or program["pattern"]
    inputs = discover_inputs(input_dir, pattern)
    if not inputs:
        print(f"No inputs found in {input_dir} matching {pattern}", file=sys.stderr)
        return 1

    tasks = []
    for path in inputs:
        rel = path.relative_to(input_dir)
        stem = path.name
        for suffix in (".scf.in", ".relax.in", ".vc-relax.in", ".in", ".inp"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        output_name = f"{stem}{program['output_suffix']}"
        task_id = f"{args.program}-{stem}".replace("_", "-").replace(".", "-")
        prompt = (
            f"You are managing a {program['program_name']} cluster calculation.\n\n"
            f"Input file: {rel}\n"
            f"Program profile: {args.program}\n"
            f"Suggested SLURM template: {program['template']}\n\n"
            "Checklist:\n"
            "1. Confirm the input and any required basis, pseudopotential, or auxiliary files are present.\n"
            "2. Stage the calculation under the configured remote project directory.\n"
            "3. Submit using the configured cluster profile and the relevant SLURM template.\n"
            "4. Monitor until completion or failure.\n"
            "5. Harvest output, logs, and restart artifacts into the calculation output directory.\n"
            "6. Summarize convergence, warnings, final energy, and next recommended action.\n"
        )
        tasks.append(
            {
                "id": task_id,
                "lane": args.lane,
                "priority": args.priority,
                "write_set": [
                    f"{args.output_dir}/{output_name}",
                    f"{args.log_dir}/{stem}.log",
                    f"{args.report_dir}/{stem}.md",
                ],
                "metadata": {
                    "domain": "computational_chemistry",
                    "program": args.program,
                    "input_dir": str(input_dir),
                    "input_file": str(rel),
                    "slurm_template": program["template"],
                    "expected_output": f"{args.output_dir}/{output_name}",
                },
                "prompt": prompt,
            }
        )

    payload = {
        "tasks": tasks,
        "metadata": {
            "domain": "computational_chemistry",
            "program": args.program,
            "source_input_dir": str(input_dir),
            "input_count": len(tasks),
        },
    }
    write_json(args.output, payload)
    if str(args.output) != "-":
        print(f"Wrote {len(tasks)} SMI task(s) to {args.output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-chem")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("make-manifest", help="Create a cluster manifest from chemistry input files.")
    p.add_argument("--program", choices=sorted(PROGRAM_DEFAULTS), default="qe")
    p.add_argument("--input-dir", required=True)
    p.add_argument("--pattern")
    p.add_argument("--output", required=True)
    p.set_defaults(func=make_manifest)

    p = sub.add_parser("make-spec", help="Create an SMI task spec from chemistry input files.")
    p.add_argument("--program", choices=sorted(PROGRAM_DEFAULTS), required=True)
    p.add_argument("--input-dir", required=True)
    p.add_argument("--pattern")
    p.add_argument("--output", required=True)
    p.add_argument("--lane", default="remote_cluster")
    p.add_argument("--priority", type=int, default=100)
    p.add_argument("--output-dir", default="outputs")
    p.add_argument("--log-dir", default="logs")
    p.add_argument("--report-dir", default="reports")
    p.set_defaults(func=make_spec)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
