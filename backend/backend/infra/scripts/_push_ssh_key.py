"""
Push the local SSH public key to all K8s nodes via password auth.
"""
import os, sys

NODES = [
    "192.168.148.153",  # master
    "192.168.148.158",  # worker1
    "192.168.148.155",  # worker2
]
SSH_USER = "kube"
SSH_PASS = "kube"
PUB_KEY_PATH = os.path.join(os.path.expanduser("~"), ".ssh", "id_ed25519.pub")

def main():
    try:
        import paramiko
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko", "-q"])
        import paramiko

    with open(PUB_KEY_PATH) as f:
        pub_key = f.read().strip()

    cmd = (
        f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        f"grep -qxF '{pub_key}' ~/.ssh/authorized_keys 2>/dev/null || "
        f"echo '{pub_key}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
    )

    for ip in NODES:
        print(f"  -> {ip} ... ", end="", flush=True)
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(ip, username=SSH_USER, password=SSH_PASS, timeout=10)
            _, stdout, stderr = client.exec_command(cmd)
            stdout.channel.recv_exit_status()
            client.close()
            print("OK")
        except Exception as e:
            print(f"ERREUR: {e}")
            sys.exit(1)

    print("Cle SSH poussee sur tous les noeuds.")

if __name__ == "__main__":
    main()
