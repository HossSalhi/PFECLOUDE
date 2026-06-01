"""
Deploy pfe-frontend image to all K8s nodes and apply manifests.
Uses SSH key auth (id_ed25519) to copy and import the image.
"""
import os, sys, subprocess, time

NODES = [
    ("192.168.148.153", "master"),
    ("192.168.148.158", "worker1"),
    ("192.168.148.155", "worker2"),
]
SSH_USER = "kube"
SSH_KEY  = os.path.join(os.path.expanduser("~"), ".ssh", "id_ed25519")
TAG      = "1.0.0"
IMAGE    = f"pfe-frontend:{TAG}"
TAR_PATH = os.path.join(os.environ.get("TEMP", "/tmp"), f"pfe-frontend-{TAG}.tar")
K8S_DIR  = os.path.join(os.path.dirname(__file__), "..", "k8s")

def main():
    try:
        import paramiko
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko", "-q"])
        import paramiko

    # Step 1: docker save
    print(f"[1/3] docker save {IMAGE} -> {TAR_PATH}")
    docker = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if not os.path.exists(docker):
        docker = "docker"
    result = subprocess.run([docker, "save", "-o", TAR_PATH, IMAGE], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERREUR docker save: {result.stderr}")
        sys.exit(1)
    size_mb = round(os.path.getsize(TAR_PATH) / 1024 / 1024, 1)
    print(f"     OK ({size_mb} MB)")

    # Step 2: scp + ctr import on each node
    print("[2/3] Transfert et import sur les noeuds K8s ...")
    for ip, name in NODES:
        print(f"     -> {ip} ({name}) ...", end=" ", flush=True)
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(ip, username=SSH_USER, key_filename=SSH_KEY, timeout=15)
        except Exception as e:
            # Fallback to password
            client.connect(ip, username=SSH_USER, password="kube", timeout=15)

        # Upload via SFTP
        sftp = client.open_sftp()
        sftp.put(TAR_PATH, "/tmp/pfe-frontend.tar")
        sftp.close()

        # Import with containerd
        _, stdout, stderr = client.exec_command(
            "sudo ctr -n k8s.io images import /tmp/pfe-frontend.tar && rm /tmp/pfe-frontend.tar"
        )
        rc = stdout.channel.recv_exit_status()
        err = stderr.read().decode().strip()
        client.close()

        if rc != 0:
            print(f"ERREUR import: {err}")
            sys.exit(1)
        print("OK")

    os.remove(TAR_PATH)

    # Step 3: kubectl apply
    print("[3/3] Application des manifests Kubernetes ...")
    kubeconfig = os.path.join(os.path.expanduser("~"), ".kube", "config")
    env = {**os.environ, "KUBECONFIG": kubeconfig}

    for manifest in ["frontend-deployment.yaml", "frontend-service.yaml"]:
        path = os.path.join(K8S_DIR, manifest)
        result = subprocess.run(
            ["kubectl", "apply", "-f", path],
            capture_output=True, text=True, env=env
        )
        print(f"     {manifest}: {result.stdout.strip()}")
        if result.returncode != 0:
            print(f"ERREUR: {result.stderr}")
            sys.exit(1)

    # Set image
    result = subprocess.run(
        ["kubectl", "-n", "pfe", "set", "image", "deployment/frontend", f"frontend={IMAGE}"],
        capture_output=True, text=True, env=env
    )
    print(f"     set image: {result.stdout.strip()}")

    # Rollout status
    print("\nAttente du rollout (max 120s) ...")
    result = subprocess.run(
        ["kubectl", "-n", "pfe", "rollout", "status", "deployment/frontend", "--timeout=120s"],
        capture_output=True, text=True, env=env
    )
    print(result.stdout.strip())
    if result.returncode != 0:
        print(f"ATTENTION: {result.stderr.strip()}")
    else:
        print("\n=== Frontend deploye avec succes ===")
        print("Acces:")
        print("  http://192.168.148.153:30880  (master)")
        print("  http://192.168.148.158:30880  (worker1)")
        print("  http://192.168.148.155:30880  (worker2)")

    # Final status
    result = subprocess.run(
        ["kubectl", "-n", "pfe", "get", "pods", "-l", "app=frontend"],
        capture_output=True, text=True, env=env
    )
    print(f"\n{result.stdout.strip()}")

if __name__ == "__main__":
    main()
