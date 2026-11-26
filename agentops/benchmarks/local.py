"""Verified candidate installation and isolated local proxy attempts."""

import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
import zipfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from urllib.request import Request, urlopen

from .core import ARMS
from .environment import agent_identity, describe_environment
from .process import WindowsJob
from .protocol import completion, decode_sse, observe_runtime
from .runner import expected_identity
from .workspace import Workspace


def verify_artifact(path, expected_digest):
    path = Path(path).resolve(strict=True)
    with path.open("rb") as stream:
        actual = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected_digest:
        raise ValueError("candidate artifact digest mismatch")
    return path


def proxy_environment(config, inherited):
    env = dict(inherited)
    args = config.get("proxy_args", [])
    # Headroom's standalone server gives HEADROOM_BACKEND precedence over CLI
    # --backend. Keep an explicit benchmark backend from being silently replaced
    # by the operator's ambient proxy configuration.
    for index, arg in enumerate(args):
        if arg == "--backend":
            if index + 1 >= len(args) or args[index + 1].startswith("--"):
                raise ValueError("--backend requires a value")
            env["HEADROOM_BACKEND"] = args[index + 1]
        elif arg.startswith("--backend="):
            env["HEADROOM_BACKEND"] = arg.split("=", 1)[1]
    binding=config.get("provider_binding")
    if binding in {"anthropic","bedrock"}:
        env["HEADROOM_BACKEND"]=binding
    elif binding in {"openai","azure"}:
        # The native CLI uses "anthropic" for direct protocol routing;
        # other backend names enable translation, including inherited settings.
        env["HEADROOM_BACKEND"]="anthropic"
    if binding=="azure" and env.get("AZURE_OPENAI_ENDPOINT"):
        endpoint=env["AZURE_OPENAI_ENDPOINT"].rstrip("/")
        if endpoint.endswith("/v1"):
            endpoint=endpoint[:-3]
        if not endpoint.endswith("/openai"):
            endpoint+="/openai"
        env["OPENAI_TARGET_API_URL"]=endpoint
    return env


def proxy_command(python, port, config, arm):
    if arm not in ARMS:
        raise ValueError("unknown arm")
    args = config.get("proxy_args", [])
    if not isinstance(args, list) or any(not isinstance(a, str) for a in args):
        raise ValueError("proxy_args must be an argument list")
    protected = {"--host", "--port", "--no-optimize", "--workers"}
    if any(a.split("=", 1)[0] in protected for a in args):
        raise ValueError("proxy_args cannot override listener, worker count or arm")
    # Safe path + no user site, with a sanitized environment below. Unlike -I,
    # these flags retain our explicitly chosen PYTHONHASHSEED. UTF-8 also avoids
    # Windows redirected-console failures in Headroom's Unicode startup banner.
    command = [
        str(python),
        "-P",
        "-s",
        "-X",
        "utf8",
        "-m",
        "headroom.proxy.server",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        *args,
    ]
    if arm == ARMS[0]:
        command.append("--no-optimize")
    return command


class LocalAdapter:
    """Owns a verified install and starts a fresh proxy for each paired attempt.

    Use a pinned container for reproducible dependency resolution and enforce
    resource limits outside this process. Remote workers execute this same class.
    """

    def __init__(self, experiment, artifact, config, workspace):
        self.experiment = experiment
        self.config = config
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        source = verify_artifact(artifact, experiment["candidate"]["artifact_sha256"])
        self.artifact = self.workspace / source.name
        if source != self.artifact.resolve():
            with self.artifact.open("xb") as target, source.open("rb") as stream:
                shutil.copyfileobj(stream, target)
        verify_artifact(self.artifact, experiment["candidate"]["artifact_sha256"])
        with zipfile.ZipFile(self.artifact) as archive:
            metadata = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            self.simulator = any(
                b"Name: headroom-benchmark-fixture\n" in archive.read(name)
                for name in metadata
            )
        self.venv = self.workspace / "candidate-venv"
        # Verify prior to executing pip or candidate code.
        subprocess.run(
            [sys.executable, "-m", "venv", str(self.venv)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
        )
        self.python = self.venv / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
        pip_args = []
        if config.get("wheelhouse"):
            pip_args = [
                "--no-index",
                "--find-links",
                str(Path(config["wheelhouse"]).resolve(strict=True)),
            ]
        subprocess.run(
            [
                str(self.python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                *pip_args,
                str(self.artifact)
                + "["
                + ",".join(config.get("extras", ["proxy"]))
                + "]",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=config.get("install_timeout", 600),
        )

    @contextmanager
    def runtime(self, experiment, arm):
        verify_artifact(self.artifact, experiment["candidate"]["artifact_sha256"])
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        env = proxy_environment(self.config, os.environ)
        for key in list(env):
            if key.startswith("PYTHON"):
                del env[key]
        env.update(
            {
                "HEADROOM_OPTIMIZE": "1" if arm == ARMS[1] else "0",
                "HEADROOM_BEACON": "0",
                "PYTHONHASHSEED": str(experiment["seed"] % 2**32),
                "HEADROOM_WORKERS": "1",
                "HEADROOM_HOST": "127.0.0.1",
                "HEADROOM_PORT": str(port),
            }
        )
        # Discard process logs: upstream errors may include credentials or prompts.
        with (
            Workspace(prefix="attempt-", dir=self.workspace) as home,
            ExitStack() as resources,
        ):
            original_home = Path.home()
            # Preserve explicitly selected AWS credentials while isolating all
            # candidate caches/config. Credentials are never copied to evidence.
            for key, name in (
                ("AWS_SHARED_CREDENTIALS_FILE", "credentials"),
                ("AWS_CONFIG_FILE", "config"),
            ):
                if key not in env and (original_home / ".aws" / name).exists():
                    env[key] = str(original_home / ".aws" / name)
            env.update(
                {
                    "HOME": home,
                    "USERPROFILE": home,
                    "APPDATA": home,
                    "LOCALAPPDATA": home,
                }
            )
            for key in (
                "XDG_CACHE_HOME",
                "XDG_CONFIG_HOME",
                "XDG_DATA_HOME",
                "XDG_STATE_HOME",
            ):
                env[key] = str(Path(home) / key.lower())
            log = None
            if self.config.get("diagnostics_dir"):
                directory = Path(self.config["diagnostics_dir"])
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                path = directory / (arm + "-" + uuid.uuid4().hex + ".log")
                log = resources.enter_context(
                    open(
                        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
                    )
                )
            job = WindowsJob() if os.name == "nt" else None
            command = proxy_command(self.python, port, self.config, arm)
            if job:
                command = [
                    sys.executable,
                    "-I",
                    "-X",
                    "utf8",
                    str(Path(__file__).with_name("process.py").resolve()),
                    job.name,
                    *command,
                ]
            try:
                process = subprocess.Popen(
                    command,
                    cwd=home,
                    env=env,
                    stdout=log if log else subprocess.DEVNULL,
                    stderr=log if log else subprocess.DEVNULL,
                    start_new_session=os.name != "nt",
                )
            except BaseException:
                if job:
                    job.close()
                raise
            try:

                def request(path, body=None):
                    headers = {
                        "Content-Type": "application/json",
                        "anthropic-version": "2023-06-01",
                    }
                    token = os.environ.get("HEADROOM_PROXY_TOKEN")
                    if token:
                        headers["X-Headroom-Proxy-Token"] = token
                    if body is not None and self.config.get("api_key_env"):
                        key = os.environ[self.config["api_key_env"]]
                        protocol = self.config.get("protocol", "openai")
                        if protocol == "anthropic":
                            headers["x-api-key"] = key
                        elif protocol == "azure":
                            headers["api-key"] = key
                        else:
                            headers["Authorization"] = "Bearer " + key
                    req = Request(
                        "http://127.0.0.1:" + str(port) + path,
                        data=json.dumps(body).encode() if body is not None else None,
                        headers=headers,
                    )
                    with urlopen(
                        req, timeout=self.config.get("request_timeout", 120)
                    ) as response:
                        if body is not None and body.get("stream"):
                            return decode_sse(
                                response, self.config.get("protocol", "openai")
                            )
                        return json.load(response)

                deadline = time.monotonic() + self.config.get("startup_timeout", 120)
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("candidate exited before readiness")
                    try:
                        before = observe_runtime(request)
                        break
                    except Exception:  # noqa: BLE001 -- startup surfaces can fail until readiness deadline
                        if time.monotonic() >= deadline:
                            raise TimeoutError("candidate readiness timeout")
                        time.sleep(0.2)
                yield request, before
            finally:
                if os.name == "nt":
                    job.terminate()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    if os.name != "nt":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.wait(timeout=10)
                finally:
                    if job:
                        job.close()

    def probe(self):
        with self.runtime(self.experiment, ARMS[0]) as (_, observed):
            return {
                "artifact_sha256": self.experiment["candidate"]["artifact_sha256"],
                "runtime": observed,
                "environment": describe_environment(self.python),
                "agent": agent_identity(),
            }

    def attempt(self, experiment, task, arm):
        before_environment = describe_environment(self.python)
        with self.runtime(experiment, arm) as (request, before):
            protocol = self.config.get("protocol", "openai")
            model = self.config.get("request_model", experiment["model"])
            tool = self.config.get("benchmark_tool", "native")
            if tool == "native":
                result = completion(request, protocol, model, task)
            else:
                from .tools import attempt
                artifacts = Path(self.config.get("tool_artifacts_dir", self.workspace))
                result = attempt(tool, request, protocol, model, task,
                                 artifacts / ("harness-" + uuid.uuid4().hex), self.config)
            after = observe_runtime(request)
            if before != after or before_environment != describe_environment(
                self.python
            ):
                raise ValueError("runtime identity changed during attempt")
            identity = expected_identity(experiment)
            identity.update(
                {
                    key: before[key]
                    for key in ("rollout_registry_digest", "rollout_snapshot_digest")
                }
            )
            identity["environment"] = before_environment
            identity["agent"] = agent_identity()
            identity["model"] = result.pop("response_model")
            result.update(
                identity=identity, optimization_enabled=before["optimization_enabled"]
            )
            return result
