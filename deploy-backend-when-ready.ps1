# Deploys pfe-backend:0.2.10 (built 2026-05-23, contains today's notification + RBAC + bootstrap changes).
# Run this AFTER all 5 VMs are up and master/workers SSH-reachable.
# Prerequisite: Docker Desktop must NOT be running (WSL2 backend conflicts with VMware).

param(
    [string]$TarPath = "$env:TEMP\pfe-backend-0.2.10.tar",
    [string]$Tag     = "pfe-backend:0.2.10"
)
$ErrorActionPreference = 'Stop'
$key = "$env:USERPROFILE\.ssh\id_ed25519"
$nodes = @(
    @{ip='192.168.148.153'; name='master'},
    @{ip='192.168.148.158'; name='worker1'},
    @{ip='192.168.148.155'; name='worker2'}
)

if (-not (Test-Path $TarPath)) { throw "Tar not found: $TarPath. Re-save via: docker save -o $TarPath $Tag" }
"Tar: $TarPath  ($([math]::Round((Get-Item $TarPath).Length/1MB,1)) MB)"

# Poll until master SSH-reachable (max 5 min)
"`n[1/5] Waiting for master SSH..."
$deadline = (Get-Date).AddMinutes(5)
$sshOk = $false
while ((Get-Date) -lt $deadline) {
    $r = ssh -i $key -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=no kube@192.168.148.153 'echo OK' 2>&1
    if ($LASTEXITCODE -eq 0 -and "$r" -match 'OK') { $sshOk = $true; "   master ready"; break }
    Start-Sleep -Seconds 6
}
if (-not $sshOk) { throw "Master SSH still down after 5 min" }

# Wait until all 3 nodes show Ready (max 3 min)
"`n[2/5] Waiting for cluster Ready..."
$deadline = (Get-Date).AddMinutes(3)
while ((Get-Date) -lt $deadline) {
    $out = ssh -i $key -o BatchMode=yes -o ConnectTimeout=5 kube@192.168.148.153 'kubectl get nodes --no-headers' 2>&1
    $readyCount = (($out -split "`n") | Where-Object {$_ -match '\bReady\b' -and $_ -notmatch 'NotReady'}).Count
    if ($readyCount -ge 3) { "   $readyCount/3 nodes Ready"; break }
    Start-Sleep -Seconds 6
}

# Distribute tar to all 3 nodes
"`n[3/5] Distributing tar to nodes..."
foreach ($n in $nodes) {
    "   -> $($n.name) ($($n.ip)): scp..."
    scp -i $key -o StrictHostKeyChecking=no -o BatchMode=yes $TarPath "kube@$($n.ip):/tmp/pfe-backend.tar"
    if ($LASTEXITCODE -ne 0) { throw "scp to $($n.ip) failed" }
    "   -> $($n.name): ctr import..."
    ssh -i $key -o BatchMode=yes "kube@$($n.ip)" "echo kube | sudo -S ctr -n k8s.io images import /tmp/pfe-backend.tar && rm /tmp/pfe-backend.tar"
    if ($LASTEXITCODE -ne 0) { throw "ctr import on $($n.ip) failed" }
}

# Update deployment image
"`n[4/5] kubectl set image..."
ssh -i $key -o BatchMode=yes kube@192.168.148.153 "kubectl -n pfe set image deployment/backend backend=$Tag"

# Wait for rollout (max 3 min)
"`n[5/5] Waiting for rollout..."
ssh -i $key -o BatchMode=yes kube@192.168.148.153 'kubectl -n pfe rollout status deployment/backend --timeout=180s'

# Final state
"`n--- DONE ---"
ssh -i $key -o BatchMode=yes kube@192.168.148.153 'kubectl -n pfe get pods -l app=backend -o wide'
"`nNext: verify RBAC fix and notification log hooks with the test sequence in: see chat history."
