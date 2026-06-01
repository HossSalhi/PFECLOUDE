"""
Configure passwordless sudo for 'kube' user on all K8s nodes.
Uses password auth since we have it.
"""
import os, sys, subprocess

NODES = [
    "192.168.148.153",
    "192.168.148.158",
    "192.168.148.155",
]
SSH_USER = "kube"
SSH_PASS = "kube"
SSH_KEY  = os.path.join(os.path.expanduser("~"), ".ssh", "id_ed25519")

SUDOERS_LINE = "kube ALL=(ALL) NOPASSWD:ALL"
SUDOERS_FILE = "/etc/sudoers.d/kube-nopasswd"

def main():
    try:
        import paramiko
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko", "-q"])
        import paramiko

    for ip in NODES:
        print(f"  -> {ip} ... ", end="", flush=True)
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Connect with password
        client.connect(ip, username=SSH_USER, password=SSH_PASS, timeout=10)

        # Write sudoers file using tee with sudo -S (password via stdin)
        cmd = f"echo '{SSH_PASS}' | sudo -S tee {SUDOERS_FILE} > /dev/null && echo '{SUDOERS_LINE}' | sudo -S tee {SUDOERS_FILE} && sudo -S chmod 440 {SUDOERS_FILE} && echo OK"
        _, stdout, stderr = client.exec_command(cmd)
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        rc = stdout.channel.recv_exit_status()
        client.close()

        if "OK" in out or rc == 0:
            print("OK")
        else:
            print(f"ERREUR (rc={rc}): {err}")
            # Try with the already configured sudo
            print(f"    (sudoers may already be configured)")

    print("Configuration sudo terminee.")

if __name__ == "__main__":
    main()
