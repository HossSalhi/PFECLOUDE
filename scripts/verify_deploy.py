import paramiko
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153", username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)
def run(cmd):
    _, o, _ = c.exec_command(cmd, timeout=20)
    return o.read().decode().strip()
print("backend pods:", run("kubectl get pods -n pfe -l app=backend"))
print("image:", run("kubectl get deployment backend -n pfe -o jsonpath='{.spec.template.spec.containers[0].image}'"))
# Get one pod name then check env
pod = run("kubectl get pod -n pfe -l app=backend -o jsonpath='{.items[0].metadata.name}'")
print("pod:", pod)
if pod:
    env = run(f"kubectl exec -n pfe {pod} -- env 2>/dev/null | grep -E 'REMEDIATION_VERIFY|REMEDIATION_VERIFICATION_RETRY'")
    print("verify timing env:", env)
c.close()
