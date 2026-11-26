"""Docker and SSH transports for the same self-contained benchmark worker."""

import hashlib
import io
import re
import shlex
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path

from .cli import load_config, load_json, save_json
from .core import digest, finite_number
from .exit_evidence import exit_matches, finalize_exit


def stage_inputs(args, stage):
    stage = Path(stage)
    (stage / "agentops/benchmarks").mkdir(parents=True)
    (stage / "agentops/__init__.py").write_text("", encoding="utf-8")
    for source in Path(__file__).parent.glob("*.py"):
        shutil.copyfile(source, stage / "agentops/benchmarks" / source.name)
    artifact = Path(args.artifact).resolve(strict=True)
    if not artifact.name.endswith(".whl"):
        raise ValueError("candidate must be an immutable wheel")
    shutil.copyfile(artifact, stage / artifact.name)
    config = load_config(args.config)
    if config.get("wheelhouse"):
        destination = stage / "wheelhouse"
        destination.mkdir()
        for source in Path(config["wheelhouse"]).glob("*.whl"):
            shutil.copyfile(source, destination / source.name)
        config["wheelhouse"] = "wheelhouse"
    save_json(stage / "runner.json", config)
    inner = [args.command]
    if args.command == "run":
        save_json(stage / "experiment.json", load_json(args.manifest))
        inner += ["experiment.json", "--lane", args.lane]
    else:
        inner += ["--sha256", args.sha256, "--seed", str(args.seed)]
    command = inner + [
        "--artifact",
        artifact.name,
        "--config",
        "runner.json",
        "--output",
        "results",
    ]
    if args.command == "run" and getattr(args, "baseline_artifact", None):
        baseline = Path(args.baseline_artifact).resolve(strict=True)
        if baseline.suffix != ".whl":
            raise ValueError("baseline must be an immutable wheel")
        target = stage / "baseline"
        target.mkdir()
        shutil.copyfile(baseline, target / baseline.name)
        baseline_config = load_config(getattr(args, "baseline_config", None) or args.config)
        if baseline_config.get("wheelhouse"):
            wheelhouse = target / "wheelhouse"
            wheelhouse.mkdir()
            for source in Path(baseline_config["wheelhouse"]).glob("*.whl"):
                shutil.copyfile(source, wheelhouse / source.name)
            baseline_config["wheelhouse"] = "wheelhouse"
        save_json(target / "runner.json", baseline_config)
        command += ["--baseline-artifact", "baseline/" + baseline.name,
                    "--baseline-config", "baseline/runner.json"]
    return command


def docker_command(args, stage, image_id, inner, container_name=None):
    for value in (args.cpus, args.memory_gib):
        if not finite_number(value) or value <= 0:
            raise ValueError("Docker limits must be finite positive numbers")
    stage = str(Path(stage).resolve())
    if "," in stage:
        raise ValueError("Docker staging path cannot contain a comma")
    command = [
        "docker",
        "run",
        "--cpus",
        str(args.cpus),
        "--memory",
        str(int(args.memory_gib * 1024**3)),
        "--memory-swap",
        str(int(args.memory_gib * 1024**3)),
        "--pids-limit",
        "256",
        "--mount",
        f"type=bind,source={stage},target=/work",
        "--workdir",
        "/work",
        "--env",
        "HEADROOM_BENCH_RUNNER=docker",
        "--env",
        "HEADROOM_BENCH_IMAGE_DIGEST=" + image_id,
    ]
    if container_name:
        if not re.fullmatch(r"headroom-benchmark-[a-f0-9]{32}", container_name):
            raise ValueError("invalid benchmark container name")
        command += ["--name", container_name]
    for name in args.env:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or name.startswith(
            "HEADROOM_BENCH_"
        ):
            raise ValueError(
                "--env accepts variable names, not values or worker identity overrides"
            )
        command += ["--env", name]
    return command + [image_id, *inner]


def ssh_command(args, remote_args):
    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:@[A-Za-z0-9][A-Za-z0-9_.-]*)?", args.host or ""
    ):
        raise ValueError("SSH host must be a hostname or user@hostname")
    if type(args.ssh_port) is not int or not 1 <= args.ssh_port <= 65535:
        raise ValueError("invalid SSH port")
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-p",
        str(args.ssh_port),
    ]
    if getattr(args, "ssh_config", None):
        command += ["-F", str(Path(args.ssh_config).resolve(strict=True))]
    return command + ["--", args.host, shlex.join(remote_args)]


def _copy_results(source, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    summary_path = Path(source) / "summary.json"
    if summary_path.exists():
        summary = load_json(summary_path)
        if not re.fullmatch(r"[a-f0-9]{64}\.json", summary["result_file"]):
            raise ValueError("invalid worker result filename")
        raw = Path(source) / summary["result_file"]
        if not raw.is_file():
            raise ValueError(
                "summary requires raw evidence in the same collection batch"
            )
        result = load_json(raw)
        byte_digest = "sha256:" + hashlib.sha256(raw.read_bytes()).hexdigest()
        if (
            digest(result) != byte_digest
            or byte_digest != summary["result_sha256"]
            or result["decision"]["status"] != summary["status"]
        ):
            raise ValueError("worker result does not match receipt")
    for path in Path(source).glob("*.json"):
        if path.name not in (
            "summary.json",
            "inspection.json",
            "worker.json",
        ) and not re.fullmatch(r"[a-f0-9]{64}\.json", path.name):
            continue
        data = load_json(path)
        if re.fullmatch(r"[a-f0-9]{64}\.json", path.name) and (
            digest(data) != "sha256:" + path.stem
            or "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            != "sha256:" + path.stem
        ):
            raise ValueError("worker evidence digest mismatch")
        destination = output / path.name
        if (
            re.fullmatch(r"[a-f0-9]{64}\.json", path.name)
            and destination.exists()
            and destination.read_bytes() != path.read_bytes()
        ):
            raise ValueError("immutable evidence collision")
        shutil.copyfile(path, destination)


REMOTE_WORKER = Path(__file__).with_name("remote_worker.py").read_text(encoding="utf-8")


def collect_remote(receipt, output):
    from argparse import Namespace

    info = load_json(receipt) if not isinstance(receipt, dict) else receipt
    if not re.fullmatch(r"/tmp/headroom-benchmark-[a-f0-9]{32}", info["remote_path"]):
        raise ValueError("invalid remote receipt")
    args = Namespace(**info)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    script = """import io,json,os,pathlib,sys,zipfile
root=pathlib.Path(sys.argv[1]); p=root/'results'
try: state=json.loads((p/'worker.json').read_text())
except (OSError,ValueError): state={'status':'unavailable'}
state['observed_running']=False
for key in ('pid','child_pid'):
 try:
  pid=state[key]; os.kill(pid,0); proc=pathlib.Path('/proc')/str(pid)
  command=(proc/'cmdline').read_bytes().split(b'\\0')
  owned=(str(root/'remote_worker.py').encode() in command) if key=='pid' else (b'agentops.benchmarks.cli' in command and (proc/'cwd').resolve()==root)
  state['observed_running']=state['observed_running'] or owned
 except (OSError,KeyError,TypeError): pass
b=io.BytesIO()
with zipfile.ZipFile(b,'w') as z:
 z.writestr('worker.json',json.dumps(state))
 if state.get('status')=='terminal' or not state['observed_running']:
  for f in p.glob('*.json'):
   if f.name!='worker.json': z.write(f,f.name)
sys.stdout.buffer.write(b.getvalue())
"""
    try:
        response = subprocess.run(
            ssh_command(args, ["python3", "-c", script, info["remote_path"]]),
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        response = None
    if response is None or response.returncode:
        save_json(
            output / "transport.json",
            {
                "status": "inconclusive",
                "reason": "SSH collection unavailable",
                "remote_path": info["remote_path"],
            },
        )
        return 75
    with tempfile.TemporaryDirectory(prefix="bench-collect-") as stage:
        with zipfile.ZipFile(io.BytesIO(response.stdout)) as archive:
            received = set(archive.namelist())
            for entry in archive.infolist():
                if (
                    not re.fullmatch(
                        r"(?:[a-f0-9]{64}|summary|inspection|worker)\.json",
                        entry.filename,
                    )
                    or entry.file_size > 100 * 1024**2
                ):
                    raise ValueError("unexpected remote output")
                (Path(stage) / entry.filename).write_bytes(archive.read(entry))
        worker = (
            load_json(Path(stage) / "worker.json") if "worker.json" in received else {}
        )
        if (
            worker.get("status") != "terminal"
            and worker.get("observed_running") is True
        ):
            save_json(output / "worker.json", worker)
            save_json(
                output / "transport.json",
                {
                    "status": "inconclusive",
                    "reason": "worker terminal exit unavailable",
                },
            )
            return 75 if worker.get("observed_running") is True else 2
        execution = {
            "transport": "ssh",
            "status": "exited"
            if worker.get("status") == "terminal"
            and type(worker.get("exit_code")) is int
            else "unverified",
            "exit_code": worker.get("exit_code"),
            "oom_killed": False,
            "error": worker.get("error", True),
        }
        try:
            _copy_results(stage, output)
        except (OSError, ValueError) as exc:
            save_json(
                output / "transport.json",
                {
                    "status": "inconclusive",
                    "reason": "invalid worker evidence",
                    "error_type": type(exc).__name__,
                },
            )
            return 2
    code = 2
    if "summary.json" in received:
        summary = load_json(output / "summary.json")
        raw = load_json(output / summary["result_file"])
        if (
            info.get("manifest_digest")
            and raw["manifest_digest"] != info["manifest_digest"]
        ):
            raise ValueError("SSH evidence belongs to another experiment")
        summary = finalize_exit(raw, execution, output, info.get("cancelled", False))
        save_json(output / "summary.json", summary)
        code = {"pass": 0, "fail": 1, "inconclusive": 2}[summary["status"]]
    elif "inspection.json" in received:
        if (
            info.get("artifact_sha256")
            and load_json(output / "inspection.json")["artifact_sha256"]
            != info["artifact_sha256"]
        ):
            raise ValueError("SSH inspection artifact drift")
        code = 0 if exit_matches(execution, "pass") and not info.get("cancelled") else 2
    save_json(
        output / "transport.json",
        {
            "transport": "ssh",
            "status": "inconclusive" if code == 2 else "complete",
            "execution": execution,
        },
    )
    return code


def cancel_remote(args, remote_path):
    script = """import json,os,pathlib,signal,sys,time
root=pathlib.Path(sys.argv[1]); state=root/'results/worker.json'
if not state.exists(): sys.exit(0)
record=json.loads(state.read_text())
def owned(pid,supervisor):
 try:
  proc=pathlib.Path('/proc')/str(pid); command=(proc/'cmdline').read_bytes().split(b'\\0')
  return (str(root/'remote_worker.py').encode() in command) if supervisor else (b'agentops.benchmarks.cli' in command and (proc/'cwd').resolve()==root)
 except (OSError,TypeError): return False
if record.get('status')=='running':
 for key in ('pid','child_pid'):
  pid=record.get(key); supervisor=(key=='pid')
  if pid and owned(pid,supervisor):
   os.kill(pid,signal.SIGTERM)
   deadline=time.monotonic()+75
   while owned(pid,supervisor) and time.monotonic()<deadline: time.sleep(0.1)
   break
"""
    subprocess.run(
        ssh_command(args, ["python3", "-c", script, remote_path]),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=90,
        check=False,
    )


def execute_transport(args):
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.transport == "docker":
        from .docker_transport import execute_docker

        return execute_docker(args)
    with tempfile.TemporaryDirectory(prefix="headroom-transport-") as temporary:
        stage = Path(temporary) / "work"
        inner = stage_inputs(args, stage)
        remote_path = "/tmp/headroom-benchmark-" + uuid.uuid4().hex
        (stage / "remote_worker.py").write_text(REMOTE_WORKER, encoding="utf-8")
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
            for source in stage.rglob("*"):
                if source.is_file():
                    package.write(source, source.relative_to(stage).as_posix())
        script = """import io,pathlib,sys,zipfile
assert sys.version_info >= (3,12), 'Python 3.12+ required'
p=pathlib.Path(sys.argv[1]); p.mkdir(mode=0o700)
with zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())) as z:
 for name in z.namelist():
  target=(p/name).resolve()
  if not target.is_relative_to(p): raise ValueError('invalid input path')
 z.extractall(p)
"""
        subprocess.run(
            ssh_command(args, ["python3", "-c", script, remote_path]),
            input=archive.getvalue(),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
        )
        receipt = {
            "kind": "ssh",
            "host": args.host,
            "ssh_port": args.ssh_port,
            "ssh_config": str(Path(args.ssh_config).resolve())
            if getattr(args, "ssh_config", None)
            else None,
            "remote_path": remote_path,
            "cancelled": False,
        }
        if args.command == "run":
            receipt["manifest_digest"] = digest(load_json(args.manifest))
        else:
            receipt["artifact_sha256"] = args.sha256
        save_json(output / "remote-receipt.json", receipt)
        try:
            subprocess.run(
                ssh_command(
                    args, ["python3", remote_path + "/remote_worker.py", *inner]
                ),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except KeyboardInterrupt:
            receipt["cancelled"] = True
            save_json(output / "remote-receipt.json", receipt)
            try:
                cancel_remote(args, remote_path)
            except (OSError, subprocess.SubprocessError):
                pass  # retain the handle even when cancellation cannot be observed
        except (OSError, subprocess.SubprocessError):
            receipt["observation_lost"] = True
        save_json(output / "remote-receipt.json", receipt)
        return collect_remote(receipt, output)
