"""
Récupère le kubeconfig depuis le master K8s et l'enregistre dans ~/.kube/config
Usage : python infra/scripts/_get_kubeconfig.py
"""
import os, sys, socket

MASTER_IP = "192.168.148.153"
SSH_USER  = "kube"
SSH_PASS  = "kube"
OUT_PATH  = os.path.join(os.path.expanduser("~"), ".kube", "config")

def main():
    try:
        import paramiko
    except ImportError:
        print("Installation de paramiko...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko", "-q"])
        import paramiko

    # Test connectivité
    try:
        sock = socket.create_connection((MASTER_IP, 22), timeout=8)
        sock.close()
    except Exception as e:
        print(f"ERREUR : Impossible de joindre {MASTER_IP}:22 — {e}")
        sys.exit(1)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(MASTER_IP, username=SSH_USER, password=SSH_PASS, timeout=10)
    except Exception as e:
        print(f"ERREUR SSH : {e}")
        sys.exit(1)

    # Lire kubeconfig
    _, stdout, stderr = client.exec_command("cat ~/.kube/config")
    config = stdout.read().decode()
    err    = stderr.read().decode()
    client.close()

    if not config or "apiVersion" not in config:
        print(f"ERREUR : kubeconfig vide ou invalide\n{err}")
        sys.exit(1)

    # Remplacer 127.0.0.1 par l'IP du master pour accès distant
    config = config.replace("https://127.0.0.1:", f"https://{MASTER_IP}:")
    config = config.replace("https://localhost:", f"https://{MASTER_IP}:")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", newline="\n") as f:
        f.write(config)

    print(f"Kubeconfig enregistre : {OUT_PATH}")
    print(f"Test : kubectl get nodes")

if __name__ == "__main__":
    main()
