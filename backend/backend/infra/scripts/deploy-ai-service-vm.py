#!/usr/bin/env python3
import argparse
import pathlib
import shutil
import sys
import tarfile
import tempfile
from typing import Iterable

import paramiko


INCLUDED_PATHS = (
    "app",
    "datasets",
    "model_artifacts",
    "scripts",
    "requirements.txt",
    "requirements-vm.txt",
    "README.md",
    "INTEGRATION.md",
)


def safe_print(message: str) -> None:
    sys.stdout.buffer.write((message + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def build_bundle(source_dir: pathlib.Path) -> pathlib.Path:
    temp_dir = pathlib.Path(tempfile.mkdtemp(prefix="pfe-ai-service-bundle-"))
    bundle_path = temp_dir / "pfe-ai-service-bundle.tar.gz"

    with tarfile.open(bundle_path, "w:gz") as archive:
        for relative in INCLUDED_PATHS:
            path = source_dir / relative
            if not path.exists():
                continue
            archive.add(path, arcname=relative)

    return bundle_path


def connect(host: str, username: str, password: str) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        username=username,
        password=password,
        look_for_keys=False,
        allow_agent=False,
        timeout=20,
        auth_timeout=20,
        banner_timeout=20,
    )
    return client


def run_command(client: paramiko.SSHClient, command: str, timeout: int = 600) -> tuple[int, str, str]:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    return code, out, err


def upload_file(sftp: paramiko.SFTPClient, local_path: pathlib.Path, remote_path: str) -> None:
    sftp.put(str(local_path), remote_path)


def stream_outputs(outputs: Iterable[str]) -> None:
    for output in outputs:
        text = output.strip()
        if text:
            safe_print(text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy the local ai-service tree to the AI VM and restart pfe-ai-service.")
    parser.add_argument("--host", default="192.168.148.161")
    parser.add_argument("--username", default="kube")
    parser.add_argument("--password", default="kube")
    parser.add_argument("--source-dir", default=str(pathlib.Path(__file__).resolve().parents[2] / "ai-service"))
    parser.add_argument("--remote-dir", default="/opt/pfe-ai-service")
    parser.add_argument("--remote-bundle", default="/tmp/pfe-ai-service-bundle.tar.gz")
    args = parser.parse_args()

    source_dir = pathlib.Path(args.source_dir).resolve()
    if not source_dir.exists():
        raise SystemExit(f"Source directory not found: {source_dir}")

    bundle_path = build_bundle(source_dir)
    safe_print(f"Bundled ai-service from {source_dir}")

    client = connect(args.host, args.username, args.password)
    try:
        sftp = client.open_sftp()
        try:
            upload_file(sftp, bundle_path, args.remote_bundle)
        finally:
            sftp.close()
        safe_print(f"Uploaded bundle to {args.host}:{args.remote_bundle}")

        sudo = f"printf '%s\\n' '{args.password}' | sudo -S "
        remote_dir = args.remote_dir.rstrip("/")
        command = " && ".join(
            [
                sudo + f"mkdir -p {remote_dir}",
                sudo + f"rm -rf {remote_dir}/app {remote_dir}/datasets {remote_dir}/model_artifacts {remote_dir}/scripts",
                sudo + f"rm -f {remote_dir}/requirements.txt {remote_dir}/requirements-vm.txt {remote_dir}/README.md {remote_dir}/INTEGRATION.md",
                sudo + f"tar -xzf {args.remote_bundle} -C {remote_dir}",
                sudo + f"chown -R aiops:aiops {remote_dir}",
                sudo + f"test -x {remote_dir}/.venv/bin/python || sudo -u aiops python3 -m venv {remote_dir}/.venv",
                sudo + f"{remote_dir}/.venv/bin/pip install --disable-pip-version-check -r {remote_dir}/requirements-vm.txt",
                sudo + "systemctl restart pfe-ai-service.service",
                sudo + "systemctl --no-pager --full status pfe-ai-service.service",
                sudo + "curl -s http://127.0.0.1:8000/health",
            ]
        )

        code, out, err = run_command(client, command, timeout=1800)
        stream_outputs([out, err])
        if code != 0:
            return code

        safe_print("AI service redeployed successfully.")
        return 0
    finally:
        client.close()
        shutil.rmtree(bundle_path.parent, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
