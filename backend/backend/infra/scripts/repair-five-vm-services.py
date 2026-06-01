#!/usr/bin/env python3
import argparse
import pathlib
import sys

import paramiko


def safe_print(text, stream=sys.stdout):
    encoded = text.encode("utf-8", errors="replace")
    decoded = encoded.decode("utf-8", errors="replace")
    print(decoded, file=stream)


def run_remote_script(host, username, password, local_script, environment):
    script_path = pathlib.Path(local_script)
    remote_path = f"/tmp/{script_path.name}"

    print(f"==> Repairing {host} with {script_path}")
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

    try:
        with client.open_sftp() as sftp:
            sftp.put(str(script_path), remote_path)

        env_prefix = " ".join(f"{key}='{value}'" for key, value in environment.items())
        command = (
            f"chmod +x {remote_path} && "
            f"printf '%s\\n' '{password}' | sudo -S env {env_prefix} {remote_path}"
        )
        stdin, stdout, stderr = client.exec_command(command, timeout=300)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()

        if out:
            safe_print(out.rstrip())
        if err:
            safe_print(err.rstrip(), stream=sys.stderr)
        if exit_code != 0:
            raise RuntimeError(f"Remote repair failed on {host} with exit code {exit_code}")
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description="Repair persistent services/routes on the five-VM AIOps lab.")
    parser.add_argument("--username", default="kube")
    parser.add_argument("--password", default="kube")
    parser.add_argument("--monitoring-ip", default="192.168.148.160")
    parser.add_argument("--ai-camunda-ip", default="192.168.148.161")
    parser.add_argument("--worker1-ip", default="192.168.148.158")
    parser.add_argument("--worker2-ip", default="192.168.148.155")
    parser.add_argument("--repo-root", default=str(pathlib.Path(__file__).resolve().parents[2]))
    args = parser.parse_args()

    repo_root = pathlib.Path(args.repo_root)
    monitoring_script = repo_root / "infra" / "vms" / "scripts" / "repair-monitoring-vm.sh"
    ai_script = repo_root / "infra" / "vms" / "scripts" / "repair-ai-camunda-vm.sh"

    common_env = {
        "WORKER1_IP": args.worker1_ip,
        "WORKER2_IP": args.worker2_ip,
    }
    run_remote_script(
        args.monitoring_ip,
        args.username,
        args.password,
        monitoring_script,
        common_env,
    )
    run_remote_script(
        args.ai_camunda_ip,
        args.username,
        args.password,
        ai_script,
        {**common_env, "MONITORING_VM_IP": args.monitoring_ip},
    )


if __name__ == "__main__":
    main()
