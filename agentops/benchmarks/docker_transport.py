"""Durable, owned Docker executions and collection without restarting workers."""

import json
import re
import subprocess
import uuid
from pathlib import Path

from .cli import load_json, save_json
from .core import digest
from .exit_evidence import exit_matches, finalize_exit
from .transports import _copy_results, docker_command, stage_inputs


def collect_docker(receipt, output):
    info = load_json(receipt) if not isinstance(receipt, dict) else dict(receipt)
    if not re.fullmatch(r"headroom-benchmark-[a-f0-9]{32}", info["container_name"]):
        raise ValueError("invalid Docker receipt")
    stage = Path(info["stage"]).resolve()
    if stage.name != ".docker-" + info["container_name"].removeprefix(
        "headroom-benchmark-"
    ):
        raise ValueError("Docker staging identity does not match receipt")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    execution = info.get("execution")
    remove_id = None
    if execution is None:
        try:
            observed = subprocess.run(
                ["docker", "container", "inspect", info["container_name"]],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            save_json(
                output / "transport.json",
                {
                    "transport": "docker",
                    "status": "inconclusive",
                    "reason": "container observation unavailable",
                },
            )
            return 75
        if observed.returncode == 0:
            container = json.loads(observed.stdout)[0]
            if container["Image"] != info["image_digest"]:
                raise ValueError("Docker container image drift")
            state = container["State"]
            if state["Running"]:
                save_json(
                    output / "transport.json",
                    {
                        "transport": "docker",
                        "status": "inconclusive",
                        "reason": "container is still running",
                    },
                )
                return 75
            execution = {
                "transport": "docker",
                "status": state.get("Status"),
                "exit_code": state.get("ExitCode"),
                "oom_killed": state.get("OOMKilled"),
                "error": bool(state.get("Error")),
            }
            info["execution"] = execution
            remove_id = container.get("Id")
            # Persist kernel proof before removing a stopped container. This
            # receipt permits collection after a lost client or later removal.
            save_json(output / "docker-receipt.json", info)
            if (
                not isinstance(receipt, dict)
                and Path(receipt).resolve()
                != (output / "docker-receipt.json").resolve()
            ):
                save_json(receipt, info)
        elif "no such" in observed.stderr.lower():
            execution = {
                "transport": "docker",
                "status": "unverified",
                "exit_code": None,
                "oom_killed": False,
                "error": True,
            }
        else:
            save_json(
                output / "transport.json",
                {
                    "transport": "docker",
                    "status": "inconclusive",
                    "reason": "container observation unavailable",
                },
            )
            return 75
    source = stage / "results"
    try:
        _copy_results(source, output)
    except (OSError, ValueError) as exc:
        save_json(
            output / "transport.json",
            {
                "transport": "docker",
                "status": "inconclusive",
                "reason": "invalid worker evidence",
                "error_type": type(exc).__name__,
            },
        )
        return 2
    terminal = source / (
        "summary.json" if info["command"] == "run" else "inspection.json"
    )
    code = 2
    if terminal.exists():
        if info["command"] == "inspect":
            if load_json(terminal)["artifact_sha256"] != info["artifact_sha256"]:
                raise ValueError("Docker inspection artifact drift")
            code = (
                0
                if exit_matches(execution, "pass") and not info.get("cancelled")
                else 2
            )
        else:
            summary = load_json(terminal)
            raw = load_json(source / summary["result_file"])
            if (
                info.get("manifest_digest")
                and raw["manifest_digest"] != info["manifest_digest"]
            ):
                raise ValueError("Docker evidence belongs to another experiment")
            summary = finalize_exit(
                raw, execution, output, info.get("cancelled", False)
            )
            save_json(output / "summary.json", summary)
            code = {"pass": 0, "fail": 1, "inconclusive": 2}[summary["status"]]
    save_json(
        output / "transport.json",
        {
            "transport": "docker",
            "status": "inconclusive" if code == 2 else "complete",
            "image_digest": info["image_digest"],
            "cancelled": info.get("cancelled", False),
            "execution": execution,
            "client_exit_code": info.get("exit_code"),
        },
    )
    if remove_id and re.fullmatch(r"[a-f0-9]{64}", remove_id):
        # Never force removal: a concurrently running object must not be killed.
        try:
            subprocess.run(
                ["docker", "rm", remove_id],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass  # the durable receipt remains usable if cleanup is unavailable
    return code


def execute_docker(args):
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if not args.image:
        raise ValueError("Docker requires --image")
    inspected = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", args.image],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    image_id = inspected.stdout.strip()
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
        raise ValueError("cannot establish immutable Docker image")
    nonce = uuid.uuid4().hex
    stage = output / (".docker-" + nonce)
    stage.mkdir(mode=0o700)
    inner = stage_inputs(args, stage)
    receipt = {
        "kind": "docker",
        "container_name": "headroom-benchmark-" + nonce,
        "stage": str(stage),
        "image_digest": image_id,
        "command": args.command,
        "cancelled": False,
    }
    if args.command == "run":
        receipt["manifest_digest"] = digest(load_json(args.manifest))
    else:
        receipt["artifact_sha256"] = args.sha256
    path = output / "docker-receipt.json"
    save_json(path, receipt)
    try:
        process = subprocess.run(
            docker_command(args, stage, image_id, inner, receipt["container_name"]),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        receipt["exit_code"] = process.returncode
    except KeyboardInterrupt:
        receipt["cancelled"] = True
        save_json(path, receipt)
        try:
            subprocess.run(
                ["docker", "stop", "--time", "60", receipt["container_name"]],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=75,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass  # receipt and staged evidence remain for later observation
    except (OSError, subprocess.SubprocessError):
        receipt["observation_lost"] = True
    save_json(path, receipt)
    return collect_docker(receipt, output)
