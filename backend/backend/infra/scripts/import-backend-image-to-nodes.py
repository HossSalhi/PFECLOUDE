from __future__ import annotations

import argparse
import os
import posixpath
import sys
from pathlib import Path

import paramiko


NODES = (
    ("k8s-master", "192.168.148.153"),
    ("k8s-worker1", "192.168.148.158"),
    ("k8s-worker2", "192.168.148.155"),
)


def print_step(message: str) -> None:
    print(message, flush=True)


def run_remote(client: paramiko.SSHClient, command: str, sudo_password: str | None = None) -> None:
    if sudo_password is not None:
        command = f"sudo -S -p '' bash -lc {quote_bash(command)}"

    stdin, stdout, stderr = client.exec_command(command)
    if sudo_password is not None:
        stdin.write(sudo_password + "\n")
        stdin.flush()

    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()

    if out:
        print(out, flush=True)
    if err:
        print(err, file=sys.stderr, flush=True)

    if exit_code != 0:
        raise RuntimeError(f"Remote command failed with exit code {exit_code}: {command}")


def quote_bash(value: str) -> str:
    escaped = value.replace("'", "'\"'\"'")
    return "'" + escaped + "'"


def upload_file(sftp: paramiko.SFTPClient, local_path: Path, remote_path: str) -> None:
    file_size = local_path.stat().st_size
    transferred = 0

    def callback(sent: int, total: int) -> None:
        nonlocal transferred
        transferred = sent
        percent = (sent / total) * 100 if total else 100
        print(f"    upload {percent:5.1f}% ({sent}/{total} bytes)", end="\r", flush=True)

    sftp.put(str(local_path), remote_path, callback=callback)
    print(f"    upload 100.0% ({transferred or file_size}/{file_size} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload and import a backend image tarball to all Kubernetes nodes.")
    parser.add_argument("--image-tar", required=True, help="Local path to docker save tar archive")
    parser.add_argument("--password", required=True, help="SSH and sudo password for the kube user")
    parser.add_argument("--remote-path", default="/home/kube/pfe-backend.tar", help="Remote tarball path")
    parser.add_argument("--image-tag", default="", help="Image tag to verify after import (e.g. 0.1.11)")
    args = parser.parse_args()

    local_path = Path(args.image_tar).resolve()
    if not local_path.exists():
        raise FileNotFoundError(local_path)

    for node_name, node_ip in NODES:
        print_step(f"\n=== {node_name} ({node_ip}) ===")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=node_ip,
            username="kube",
            password=args.password,
            timeout=30,
            banner_timeout=30,
            auth_timeout=30,
        )
        try:
            with client.open_sftp() as sftp:
                upload_file(sftp, local_path, args.remote_path)

            run_remote(client, f"ctr -n k8s.io images import {args.remote_path}", sudo_password=args.password)
            tag_filter = args.image_tag if args.image_tag else "pfe-backend"
            run_remote(client, f"ctr -n k8s.io images ls | grep '{tag_filter}'", sudo_password=args.password)
        finally:
            client.close()

    print_step("\nImage upload/import completed on all nodes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
