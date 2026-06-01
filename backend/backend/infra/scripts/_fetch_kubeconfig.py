"""Run once to refresh the kubeconfig from the k8s master node.
Usage: python infra/scripts/_fetch_kubeconfig.py
"""
import os
import paramiko

MASTER_IP = "192.168.148.153"
KUBECONFIG_OUT = os.path.join(os.environ.get("TEMP", "/tmp"), "pfe-kubeconfig-master")

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(MASTER_IP, username="kube", password="kube")
_, stdout, _ = client.exec_command("cat ~/.kube/config")
config = stdout.read().decode()
client.close()

with open(KUBECONFIG_OUT, "w", newline="\n") as f:
    f.write(config)

print(f"Kubeconfig saved to {KUBECONFIG_OUT}")
print("Test: kubectl --kubeconfig \"" + KUBECONFIG_OUT + "\" get nodes")
