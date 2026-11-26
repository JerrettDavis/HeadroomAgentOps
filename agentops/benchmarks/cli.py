"""Standalone benchmark CLI; does not start or mutate review services."""

import argparse
import hashlib
import json
import re
import signal
import subprocess
import sys
from pathlib import Path

from .core import digest, schedule, validate_manifest
from .local import LocalAdapter
from .runner import run_experiment, write_evidence
from .workspace import Workspace


def load_json(path):
    return load_json_text(Path(path).read_text("utf-8"))


def load_json_text(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError("nonfinite JSON number")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def load_config(path):
    data = load_json(path) if path else {}
    allowed = {
        "protocol",
        "proxy_args",
        "wheelhouse",
        "install_timeout",
        "request_timeout",
        "startup_timeout",
        "request_model",
        "api_key_env",
        "extras",
        "diagnostics_dir",
        "benchmark_tool",
        "tool_python",
        "provider_binding",
    }
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError("unknown runner configuration fields")
    if data.get("benchmark_tool", "native") not in {"native", "inspect", "aiperf"}:
        raise ValueError("unsupported benchmark tool")
    if "provider_binding" in data and data["provider_binding"] not in {"openai","anthropic","azure","bedrock"}:
        raise ValueError("unsupported explicit provider binding")
    if "tool_python" in data and (not isinstance(data["tool_python"], str) or not data["tool_python"].strip()
                                  or len(data["tool_python"]) > 4096 or any(ord(c) < 32 for c in data["tool_python"])):
        raise ValueError("tool_python must identify a runner Python executable")
    if data.get("protocol", "openai") not in (
        "openai",
        "anthropic",
        "azure",
        "bedrock",
    ):
        raise ValueError("unsupported provider protocol")
    for key in ("install_timeout", "request_timeout", "startup_timeout"):
        value = data.get(key, 120)
        if type(value) is not int or not 1 <= value <= 3600:
            raise ValueError("timeouts must be integer seconds between 1 and 3600")
    if "api_key_env" in data and not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*", data["api_key_env"]
    ):
        raise ValueError("api_key_env must name an environment variable")
    extras = data.get("extras", ["proxy"])
    if (
        not isinstance(extras, list)
        or not extras
        or any(
            not isinstance(e, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", e)
            for e in extras
        )
    ):
        raise ValueError("extras must be a list of package extra names")
    if "wheelhouse" in data:
        location = Path(data["wheelhouse"])
        if not location.is_absolute():
            location = Path(path).resolve().parent / location
        data["wheelhouse"] = str(location.resolve(strict=True))
    # Validate controls before candidate installation.
    from .local import proxy_command

    proxy_command(sys.executable, 8787, data, "b_headroom")
    return data


class SetupFailure:
    def __init__(self, error):
        self.error = error

    def attempt(self, *args):
        raise self.error


def run_local(args):
    experiment = load_json(args.manifest)
    if isinstance(experiment, dict) and experiment.get("schema_version") == 2:
        from .comparison import run_local_comparison
        return run_local_comparison(args)
    validate_manifest(experiment)
    config = load_config(args.config)
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if config.get("benchmark_tool", "native") != "native":
        config["tool_artifacts_dir"] = str(output / "tool-evidence")
    with Workspace(prefix="candidate-", dir=output) as workspace:
        setup_error = None
        try:
            adapter = LocalAdapter(experiment, args.artifact, config, workspace)
        except KeyboardInterrupt:
            adapter = SetupFailure(KeyboardInterrupt())
            setup_error = "cancelled"
        except Exception as exc:  # noqa: BLE001 -- preserve terminal evidence for arbitrary installer failures
            adapter = SetupFailure(exc)
            setup_error = type(exc).__name__
        result = run_experiment(experiment, adapter)
        result["runner_config_digest"] = digest(
            {
                key: value
                for key, value in config.items()
                if key not in ("diagnostics_dir", "wheelhouse", "tool_artifacts_dir")
            }
        )
        lane = "simulator" if getattr(adapter, "simulator", False) else args.lane
        result["lane"] = lane
        if setup_error:
            result["setup_error"] = setup_error
        if lane == "simulator":
            result["decision"] = {
                "status": "inconclusive",
                "reason": "simulator is not live qualification evidence",
                "simulated_decision": result["decision"],
            }
        path = write_evidence(result, output)
    summary = {
        "status": result["decision"]["status"],
        "result_file": path.name,
        "result_sha256": digest(result),
        "lane": lane,
    }
    save_json(output / "summary.json", summary)
    print(json.dumps(summary, sort_keys=True))
    return {"pass": 0, "fail": 1, "inconclusive": 2}[summary["status"]]


def parser():
    root = argparse.ArgumentParser(prog="headroom-benchmark", description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    demonstration = commands.add_parser("demo", help="run an offline Studio simulation without provider calls")
    demonstration.add_argument("--design", required=True)
    demonstration.add_argument("--output", required=True)
    source = commands.add_parser("resolve-source", help="explicitly fetch a pinned artifact or build a pinned commit in Docker")
    source.add_argument("--source", required=True)
    source.add_argument("--output", required=True)
    group = commands.add_parser("group", help="execute a preflighted immutable Studio experiment matrix")
    group.add_argument("--plan-file", required=True)
    group.add_argument("--plan-digest", required=True)
    group.add_argument("--output", required=True)
    tool = commands.add_parser("prepare-local-tool", help="explicitly install a pinned harness in a managed environment")
    tool.add_argument("--tool", choices=("inspect", "aiperf"), required=True)
    tool.add_argument("--root", required=True)
    tool.add_argument("--output", required=True)
    setup = commands.add_parser("prepare-tool", help="explicitly prepare a local, Docker or SSH harness runtime")
    setup.add_argument("--setup", required=True)
    setup.add_argument("--root", required=True)
    setup.add_argument("--output", required=True)
    export = commands.add_parser(
        "export-dataset", help="export private task inputs for an OSS harness"
    )
    export.add_argument("tasks")
    export.add_argument("--format", choices=("inspect", "aiperf"), required=True)
    export.add_argument("--output", required=True)
    imported = commands.add_parser(
        "import-inspect", help="import literal-target text Inspect Sample JSONL"
    )
    imported.add_argument("dataset")
    imported.add_argument("--taskset-id", required=True)
    imported.add_argument("--revision", required=True)
    imported.add_argument("--output", required=True)
    plan = commands.add_parser(
        "plan", help="validate and write a seeded paired schedule"
    )
    plan.add_argument("manifest")
    plan.add_argument("--output", required=True)
    init = commands.add_parser(
        "init",
        help="bind observed candidate/environment identities to a taskset and policy",
    )
    for key in (
        "inspection",
        "candidate-sha",
        "model",
        "tasks",
        "policy",
        "experiment-id",
        "output",
    ):
        init.add_argument("--" + key, required=True)
    init.add_argument("--seed", type=int, default=42)
    init.add_argument("--repeats", type=int, default=5)
    collect = commands.add_parser(
        "collect", help="retrieve the same Docker or SSH run without restarting it"
    )
    collect.add_argument("receipt")
    collect.add_argument("--output", required=True)
    reference = commands.add_parser(
        "reference",
        help="export release reference in an authenticated CI publication workflow",
    )
    reference.add_argument("manifest")
    reference.add_argument("--result", required=True)
    reference.add_argument("--uri", required=True)
    reference.add_argument("--output", required=True)
    for name in ("inspect", "run"):
        cmd = commands.add_parser(
            name,
            help="observe candidate identity"
            if name == "inspect"
            else "execute paired experiment",
        )
        if name == "run":
            cmd.add_argument("manifest")
            cmd.add_argument("--baseline-artifact", help="second pinned build for a v2 comparison")
            cmd.add_argument("--baseline-config", help="optional separate baseline runner configuration")
            cmd.add_argument("--lane", choices=("live", "simulator"), default="live")
        else:
            cmd.add_argument("--sha256", required=True)
            cmd.add_argument("--seed", type=int, default=42)
        cmd.add_argument("--artifact", required=True)
        cmd.add_argument("--config")
        cmd.add_argument("--output", required=True)
        cmd.add_argument(
            "--transport", choices=("local", "docker", "ssh"), default="local"
        )
        cmd.add_argument(
            "--image", help="Docker image, resolved to immutable ID before execution"
        )
        cmd.add_argument("--cpus", type=float, default=2)
        cmd.add_argument("--memory-gib", type=float, default=4)
        cmd.add_argument(
            "--env",
            action="append",
            default=[],
            help="environment variable NAME to expose in Docker",
        )
        cmd.add_argument("--host", help="SSH target, e.g. bench@host")
        cmd.add_argument("--ssh-port", type=int, default=22)
        cmd.add_argument(
            "--ssh-config", help="optional local OpenSSH configuration file"
        )
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    previous = {}

    def cancel(signum, frame):
        raise KeyboardInterrupt()

    # A graceful container/SSH stop follows the same evidence-preserving path
    # as Ctrl-C. SIGKILL/power loss cannot be caught.
    for name in ("SIGTERM", "SIGHUP", "SIGBREAK"):
        if hasattr(signal, name):
            signum = getattr(signal, name)
            previous[signum] = signal.signal(signum, cancel)
    try:
        if args.command == "prepare-local-tool":
            from .tools import prepare
            ready = prepare(args.tool, Path(args.root).expanduser())
            save_json(Path(args.output) / "tool.json", dict(ready, tool=args.tool))
            return 0 if ready["available"] else 64
        if args.command == "prepare-tool":
            from agentops.benchmark_tool_setup import setup, recover
            spec = load_json_text(args.setup)
            ready = (recover(spec["recover"], Path(args.root), Path(args.output))
                     if isinstance(spec, dict) and set(spec) == {"recover"} else
                     setup(spec, Path(args.root), Path(args.output)))
            save_json(Path(args.output) / "tool-setup.json", ready)
            return 0 if ready["available"] else 75 if ready.get("status") == "inconclusive" else 64
        if args.command == "group":
            from agentops.benchmark_groups import run_group
            plan = load_json(args.plan_file)
            if digest(plan) != args.plan_digest:
                raise ValueError("Immutable group input digest mismatch")
            result = run_group(plan, args.output)
            return {"pass": 0, "fail": 1, "inconclusive": 2}[result["decision"]["status"]]
        if args.command == "resolve-source":
            from agentops.benchmark_resolver import resolve
            resolve(load_json_text(args.source), args.output)
            return 0
        if args.command == "demo":
            from agentops.benchmark_studio import demo
            demo(load_json_text(args.design), args.output)
            return 2
        if args.command == "export-dataset":
            from .interop import export_dataset

            rows = export_dataset(load_json(args.tasks), args.format)
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as stream:
                for row in rows:
                    stream.write(
                        json.dumps(
                            row, sort_keys=True, ensure_ascii=False, allow_nan=False
                        )
                        + "\n"
                    )
            return 0
        if args.command == "import-inspect":
            from .interop import import_inspect

            # Reuse the strict JSON loader for duplicate keys and finite numbers.
            records = Path(args.dataset).read_text("utf-8").splitlines()
            rows = [load_json_text(line) for line in records if line.strip()]
            data = import_inspect(rows, args.taskset_id, args.revision)
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + "\n"
                )
            return 0
        if args.command == "init":
            observed, taskset = load_json(args.inspection), load_json(args.tasks)
            if set(taskset) != {"taskset_id", "revision", "tasks"}:
                raise ValueError("taskset requires id, revision and tasks")
            experiment = {
                "schema_version": 1,
                "experiment_id": args.experiment_id,
                "seed": args.seed,
                "repeats": args.repeats,
                "candidate": {
                    "sha": args.candidate_sha,
                    "artifact_sha256": observed["artifact_sha256"],
                },
                "rollout": {
                    "registry_digest": observed["runtime"]["rollout_registry_digest"],
                    "snapshot_digest": observed["runtime"]["rollout_snapshot_digest"],
                },
                "model": args.model,
                "agent": observed["agent"],
                "taskset_id": taskset["taskset_id"],
                "taskset_revision": taskset["revision"],
                "tasks": taskset["tasks"],
                "environment": observed["environment"],
                "policy": load_json(args.policy),
            }
            validate_manifest(experiment)
            save_json(args.output, experiment)
            return 0
        if args.command == "reference":
            from .reference import build_reference, github_producer

            result = load_json(args.result)
            if "sha256:" + hashlib.sha256(
                Path(args.result).read_bytes()
            ).hexdigest() != digest(result):
                raise ValueError("raw evidence is not the canonical hashed file")
            save_json(
                args.output,
                build_reference(
                    load_json(args.manifest), result, args.uri, github_producer()
                ),
            )
            return 0
        if args.command == "collect":
            from .transports import collect_remote

            receipt = load_json(args.receipt)
            if receipt.get("kind") == "docker":
                from .docker_transport import collect_docker

                return collect_docker(args.receipt, args.output)
            return collect_remote(receipt, args.output)
        if args.command == "plan":
            experiment = load_json(args.manifest)
            validate_manifest(experiment)
            save_json(
                args.output,
                {
                    "manifest_digest": digest(experiment),
                    "schedule": schedule(
                        experiment["tasks"], experiment["repeats"], experiment["seed"]
                    ),
                },
            )
            return 0
        if args.transport != "local":
            from .transports import execute_transport

            return execute_transport(args)
        if args.command == "run":
            return run_local(args)
        config = load_config(args.config)
        with Workspace(prefix="headroom-inspect-") as workspace:
            experiment = {
                "candidate": {"artifact_sha256": args.sha256},
                "seed": args.seed,
            }
            observed = LocalAdapter(
                experiment, args.artifact, config, workspace
            ).probe()
        save_json(Path(args.output) / "inspection.json", observed)
        print(json.dumps(observed, sort_keys=True))
        return 0
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as exc:
        print(
            f"benchmark input/execution error ({type(exc).__name__}); check manifest, paths and runner configuration",
            file=sys.stderr,
        )
        return 64
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
