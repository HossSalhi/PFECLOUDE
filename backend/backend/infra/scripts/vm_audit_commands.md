# VM Audit Command Pack (Run on Each VM)

Run these commands on:
- `k8s-master` (`192.168.148.153`)
- `k8s-worker1` (`192.168.148.158`)
- `k8s-worker2` (`192.168.148.155`)

```bash
hostnamectl
ip a
free -h
df -h
lsblk
swapon --show
systemctl status kubelet --no-pager
systemctl status containerd --no-pager
journalctl -u kubelet -n 200 --no-pager
journalctl -u containerd -n 200 --no-pager
timedatectl
```

Cluster-side commands from master:

```bash
kubectl get nodes -o wide
kubectl describe nodes
kubectl get ns
kubectl get pods -A -o wide
kubectl get svc -A
kubectl get deploy,statefulset,daemonset -A
kubectl get pvc,pv -A
kubectl get storageclass
kubectl top nodes
kubectl top pods -A
kubectl get events -A --sort-by=.lastTimestamp
```
