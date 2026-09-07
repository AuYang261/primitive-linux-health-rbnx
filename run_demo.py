#!/usr/bin/env python3
# SPDX-License-Identifier: MulanPSL-2.0
"""Run Atlas, Soma, Vitals, this primitive, and Robonix Client on private ports."""

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

PACKAGE_ROOT = Path(__file__).resolve().parent
RUNTIME = PACKAGE_ROOT / ".runtime"
STATE = RUNTIME / "processes.json"
PORTS = (51051, 51091, 51093, 17860)


def process_token(pid):
    """Identify a live Linux process by PID and start ticks."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        return None if fields[0] == "Z" else fields[19]
    except (OSError, IndexError):
        return None


def owned(entry):
    """Return whether an entry still identifies the same live process."""
    return process_token(entry["pid"]) == entry["start_ticks"]


def read_state():
    """Load process ownership state, tolerating the first run."""
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(state):
    """Atomically persist process ownership state."""
    RUNTIME.mkdir(exist_ok=True)
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    temporary.replace(STATE)


def start_process(name, command, environment, state):
    """Detach one process with a private log and record its identity."""
    with (RUNTIME / f"{name}.log").open("ab") as log:
        child = subprocess.Popen(
            command,
            cwd=PACKAGE_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    token = process_token(child.pid)
    if token is None:
        raise RuntimeError(f"{name} exited at launch; inspect {RUNTIME}/{name}.log")
    state[name] = {"pid": child.pid, "start_ticks": token}
    save_state(state)


def stop(state):
    """Terminate only processes recorded by this runner."""
    for name in ("client", "stack"):
        entry = state.get(name)
        if entry and owned(entry):
            os.kill(entry["pid"], signal.SIGTERM)
            deadline = time.monotonic() + 20
            while owned(entry) and time.monotonic() < deadline:
                time.sleep(0.2)
            if owned(entry):
                raise RuntimeError(f"{name} did not stop; inspect its log")
        state.pop(name, None)
        save_state(state)


def require_free_ports():
    """Refuse to modify unrelated listeners on the demo ports."""
    for port in PORTS:
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError as error:
                raise RuntimeError(f"port {port} is already in use") from error


def wait_port(port, state):
    """Wait for bounded startup while checking parent processes."""
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if any(not owned(entry) for entry in state.values()):
            raise RuntimeError(f"a process exited; inspect {RUNTIME}/*.log")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError(f"port {port} did not become ready")


def write_manifest(core_root):
    """Create a local deployment manifest with absolute package paths."""
    manifest = {
        "manifestVersion": 1,
        "name": "compute-node-health-demo",
        "system": {
            "atlas": {"listen": "127.0.0.1:51051", "log": "info"},
            "soma": {
                "listen": "127.0.0.1:51091",
                "robot_yaml": str(PACKAGE_ROOT / "deploy/local/soma.yaml"),
                "log": "info",
            },
            "vitals": {
                "listen": "127.0.0.1:51093",
                "thresholds_path": str(PACKAGE_ROOT / "deploy/local/thresholds.yaml"),
                "expected_modules": [],
                "log": "info",
            },
        },
        "primitive": [
            {
                "name": "linux_health",
                "path": str(PACKAGE_ROOT),
                "config": {
                    "interval_s": 1.0,
                    "component_prefix": "body/compute_node",
                    "display_name": "Linux compute node",
                },
            }
        ],
    }
    RUNTIME.mkdir(exist_ok=True)
    path = RUNTIME / "robonix_manifest.yaml"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return path


def start(args, state):
    """Start the complete local data path using existing core binaries."""
    if any(owned(entry) for entry in state.values()):
        raise RuntimeError("demo is already running")
    require_free_ports()
    core_root = args.core_root.resolve()
    binaries = args.bin_dir.resolve()
    client = args.client_root.resolve() / ".venv/bin/robonix-client"
    required = [client] + [binaries / name for name in (
        "rbnx", "robonix-atlas", "robonix-soma", "robonix-vitals"
    )]
    for path in required:
        if not path.is_file():
            raise RuntimeError(f"missing {path}; build core/client first")
    manifest = write_manifest(core_root)
    environment = {
        **os.environ,
        "ROBONIX_HOME": str(RUNTIME / "robonix-home"),
        "ROBONIX_CLIENT_VITALS_DB": str(RUNTIME / "vitals-alerts.sqlite3"),
        "PATH": f"{binaries}:{os.environ.get('PATH', '')}",
        "NO_PROXY": "localhost,127.0.0.1",
        "no_proxy": "localhost,127.0.0.1",
    }
    subprocess.run([str(binaries / "rbnx"), "setup", str(core_root)], env=environment, check=True)
    state.clear()
    try:
        start_process("stack", [str(binaries / "rbnx"), "boot", "-f", str(manifest)], environment, state)
        for port in PORTS[:3]:
            wait_port(port, state)
        start_process(
            "client",
            [str(client), "--host", "127.0.0.1", "--port", "17860",
             "--robot-host", "127.0.0.1", "--atlas-port", "51051"],
            environment,
            state,
        )
        wait_port(17860, state)
    except BaseException:
        stop(state)
        raise
    print("Compute-node health is available at http://127.0.0.1:17860/")


def main():
    """Expose start, stop, and status commands."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "stop", "status"))
    parser.add_argument("--core-root", type=Path, default=Path("/home/xjy/robonix"))
    parser.add_argument("--bin-dir", type=Path, default=Path("/home/xjy/robonix/target/debug"))
    parser.add_argument("--client-root", type=Path, default=Path("/home/xjy/robonix-client-linux-health"))
    args = parser.parse_args()
    state = read_state()
    if args.command == "start":
        start(args, state)
    elif args.command == "stop":
        stop(state)
    else:
        print(json.dumps({name: {**entry, "running": owned(entry)} for name, entry in state.items()}, indent=2))


if __name__ == "__main__":
    main()
