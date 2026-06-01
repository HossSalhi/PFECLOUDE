import paramiko
import time

VMS = [
    {"name": "Master",     "host": "192.168.148.153"},
    {"name": "Worker1",    "host": "192.168.148.158"},
    {"name": "Worker2",    "host": "192.168.148.155"},
    {"name": "Monitoring", "host": "192.168.148.160"},
    {"name": "AI/Camunda", "host": "192.168.148.161"},
]

USERNAME = "kube"
PASSWORD = "kube"
SUDO_PASS = "kube"


def run_cmd(client, cmd, timeout=60):
    """Run a command and return (stdout, stderr, exit_code)."""
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    code = stdout.channel.recv_exit_status()
    return out, err, code


def sudo(cmd):
    """Wrap a command with echo-piped sudo password."""
    return f"echo {SUDO_PASS} | sudo -S {cmd}"


def get_disk_usage(client):
    out, _, _ = run_cmd(client, "df -h / | tail -1")
    return out


def get_top_consumers(client):
    out, _, _ = run_cmd(client, "du --max-depth=2 -h / 2>/dev/null | sort -rh | head -20", timeout=120)
    return out


def clean_vm(vm):
    name = vm["name"]
    host = vm["host"]
    print(f"\n{'='*60}")
    print(f"  VM: {name} ({host})")
    print(f"{'='*60}")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            host,
            username=USERNAME,
            password=PASSWORD,
            timeout=15,
            look_for_keys=False,
            allow_agent=False,
        )
    except Exception as e:
        print(f"  UNREACHABLE: {e}")
        return {
            "name": name,
            "host": host,
            "status": "UNREACHABLE",
            "error": str(e),
        }

    print(f"  Connected successfully.")

    # 1. Before disk usage
    before = get_disk_usage(client)
    print(f"\n  [BEFORE] Disk usage (/):\n    {before}")

    # 2. Top consumers
    print(f"\n  [INFO] Top disk consumers (du --max-depth=2):")
    top = get_top_consumers(client)
    for line in top.splitlines():
        print(f"    {line}")

    # 3. Cleanup steps
    print(f"\n  [CLEAN] Starting cleanup...")

    steps = [
        ("apt-get clean", sudo("apt-get clean"), 60),
        ("journalctl vacuum 50M", sudo("journalctl --vacuum-size=50M"), 60),
        ("apt-get autoremove", sudo("apt-get autoremove -y"), 120),
        ("/tmp cleanup (files >1 day)", sudo("find /tmp -type f -atime +1 -delete 2>/dev/null"), 30),
        ("rm pip/huggingface caches /root", sudo("rm -rf /root/.cache/pip /root/.cache/huggingface 2>/dev/null"), 30),
        ("crictl rmi --prune", sudo("crictl rmi --prune 2>/dev/null || true"), 120),
        ("docker system prune -f", "docker system prune -f 2>/dev/null || true", 120),
    ]

    for step_name, cmd, timeout in steps:
        print(f"    -> {step_name} ...", end="", flush=True)
        out, err, code = run_cmd(client, cmd, timeout=timeout)
        if out:
            # Show trimmed output
            lines = out.splitlines()
            preview = " | ".join(lines[:3])
            if len(lines) > 3:
                preview += f" ... ({len(lines)} lines total)"
            print(f" done. Output: {preview}")
        else:
            print(f" done (no output).")
        if err and "sudo:" not in err and "WARNING" not in err.upper() and err.strip():
            # filter out sudo password prompt noise
            err_lines = [l for l in err.splitlines() if not l.startswith("[sudo]") and "password" not in l.lower()]
            if err_lines:
                print(f"      stderr: {' | '.join(err_lines[:3])}")

    # 4. After disk usage
    after = get_disk_usage(client)
    print(f"\n  [AFTER]  Disk usage (/):\n    {after}")

    client.close()

    # Parse sizes for summary
    return {
        "name": name,
        "host": host,
        "status": "OK",
        "before": before,
        "after": after,
    }


def parse_used(df_line):
    """Extract Used column (3rd field) from df -h output line."""
    parts = df_line.split()
    if len(parts) >= 5:
        return parts[2]  # Used
    return "?"


def parse_avail(df_line):
    """Extract Avail column (4th field) from df -h output line."""
    parts = df_line.split()
    if len(parts) >= 5:
        return parts[3]  # Avail
    return "?"


def parse_pct(df_line):
    """Extract Use% column (5th field) from df -h output line."""
    parts = df_line.split()
    if len(parts) >= 5:
        return parts[4]  # Use%
    return "?"


def main():
    print("=" * 60)
    print("  Kubernetes Lab VM Disk Cleanup Script")
    print(f"  VMs: {len(VMS)} nodes")
    print("=" * 60)

    results = []
    for vm in VMS:
        result = clean_vm(vm)
        results.append(result)
        time.sleep(2)  # small pause between VMs

    # Summary table
    print(f"\n\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}")
    print(f"  {'VM':<14} {'Status':<12} {'Used Before':<14} {'Used After':<14} {'Avail After':<14} {'Use%':<8}")
    print(f"  {'-'*14} {'-'*12} {'-'*14} {'-'*14} {'-'*14} {'-'*8}")
    for r in results:
        if r["status"] == "UNREACHABLE":
            print(f"  {r['name']:<14} UNREACHABLE  (could not connect: {r.get('error','?')[:40]})")
        else:
            before_used = parse_used(r["before"])
            after_used  = parse_used(r["after"])
            after_avail = parse_avail(r["after"])
            after_pct   = parse_pct(r["after"])
            print(f"  {r['name']:<14} {'OK':<12} {before_used:<14} {after_used:<14} {after_avail:<14} {after_pct:<8}")
    print()


if __name__ == "__main__":
    main()
