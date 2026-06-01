#!/usr/bin/env python3
import argparse
import pathlib
import sys
import time

import paramiko


def safe_print(text: str, stream=sys.stdout) -> None:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if hasattr(stream, "buffer"):
        stream.buffer.write(normalized.encode("utf-8", errors="replace"))
        stream.buffer.write(b"\n")
        stream.flush()
        return

    printable = normalized.encode("ascii", errors="replace").decode("ascii")
    print(printable, file=stream)


def run_command(client: paramiko.SSHClient, command: str, timeout: int = 120) -> tuple[int, str, str]:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    exit_code = stdout.channel.recv_exit_status()
    return exit_code, out, err


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Stop/start worker services to simulate a reversible worker outage.")
    parser.add_argument("--host", required=True)
    parser.add_argument("--username", default="kube")
    parser.add_argument("--password", default="kube")
    parser.add_argument("--action", choices=("stop", "start", "status"), required=True)
    parser.add_argument("--wait-seconds", type=int, default=12)
    args = parser.parse_args()

    services = ("kubelet", "containerd")
    sudo_prefix = f"printf '%s\\n' '{args.password}' | sudo -S "

    client = connect(args.host, args.username, args.password)
    try:
        if args.action == "status":
            exit_code, out, err = run_command(
                client,
                sudo_prefix + "systemctl status kubelet containerd --no-pager",
                timeout=120,
            )
            if out:
                safe_print(out.rstrip())
            if err:
                safe_print(err.rstrip(), stream=sys.stderr)
            return exit_code

        if args.action == "stop":
            command = sudo_prefix + "systemctl stop kubelet containerd"
        else:
            command = sudo_prefix + "systemctl start containerd kubelet"

        exit_code, out, err = run_command(client, command, timeout=120)
        if out:
            safe_print(out.rstrip())
        if err:
            safe_print(err.rstrip(), stream=sys.stderr)
        if exit_code != 0:
            raise RuntimeError(f"Command failed with exit code {exit_code}")

        time.sleep(max(0, args.wait_seconds))
        exit_code, out, err = run_command(
            client,
            sudo_prefix + "systemctl is-active " + " ".join(services),
            timeout=60,
        )
        safe_print(out.rstrip())
        if err:
            safe_print(err.rstrip(), stream=sys.stderr)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
