param(
    [string]$Tag          = "1.0.0",
    [string]$MasterIp     = "192.168.148.153",
    [string]$Worker1Ip    = "192.168.148.158",
    [string]$Worker2Ip    = "192.168.148.155",
    [string]$SshUser      = "ubuntu",
    [string]$FrontendDir  = "../../../../frontend",
    [switch]$SkipBuild,
    [switch]$SkipLoad
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$LASTEXITCODE = 0

$ImageName = "pfe-frontend"
$ImageFull = "${ImageName}:${Tag}"
$TarFile   = "$env:TEMP\pfe-frontend-${Tag}.tar"

# Resolve real Docker executable (bypass Windows System32 shim)
$DockerExe = if (Test-Path "C:\Program Files\Docker\Docker\resources\bin\docker.exe") {
    "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
} else { "docker" }

function Assert-Command {
    param([string]$Name)
    if ($Name -eq "docker") {
        if (-not (Test-Path $script:DockerExe -ErrorAction SilentlyContinue) -and
            -not (Get-Command $Name -ErrorAction SilentlyContinue)) {
            throw "Docker introuvable. Verifiez que Docker Desktop est installe."
        }
        return
    }
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Commande requise introuvable : $Name. Verifiez votre PATH."
    }
}

function Invoke-Ssh {
    param([string]$Ip, [string]$Cmd)
    ssh "${SshUser}@${Ip}" $Cmd
}

# ─── Vérifications préalables ───────────────────────────────────────────────
Assert-Command kubectl

$FrontendPath = Resolve-Path (Join-Path $PSScriptRoot $FrontendDir)
if (-not (Test-Path "$FrontendPath\Dockerfile")) {
    throw "Dockerfile introuvable dans $FrontendPath"
}

Write-Host ""
Write-Host "=== Deploiement frontend AIOps ===" -ForegroundColor Cyan
Write-Host "Image  : $ImageFull"
Write-Host "Nodes  : master=$MasterIp  worker1=$Worker1Ip  worker2=$Worker2Ip"
Write-Host ""

# ─── Etape 1 : Build Docker ──────────────────────────────────────────────────
if (-not $SkipBuild) {
    Assert-Command docker
    Write-Host "[1/4] Build de l'image Docker $ImageFull ..." -ForegroundColor Yellow
    Push-Location $FrontendPath
    try {
        & $DockerExe build -t $ImageFull .
        if ($LASTEXITCODE -ne 0) { throw "docker build a echoue" }
    } finally {
        Pop-Location
    }
    Write-Host "      Build OK" -ForegroundColor Green
} else {
    Write-Host "[1/4] Build ignore (--SkipBuild)" -ForegroundColor DarkGray
}

# ─── Etape 2 : Sauvegarde tar ────────────────────────────────────────────────
if (-not $SkipLoad) {
    Assert-Command docker
    Write-Host "[2/4] Sauvegarde de l'image en tar : $TarFile ..." -ForegroundColor Yellow
    & $DockerExe save -o $TarFile $ImageFull
    if ($LASTEXITCODE -ne 0) { throw "docker save a echoue" }
    $sizeMb = [math]::Round((Get-Item $TarFile).Length / 1MB, 1)
    Write-Host "      Tar OK ($sizeMb MB)" -ForegroundColor Green

    # ─── Etape 3 : Copie + chargement sur chaque noeud ───────────────────────
    Write-Host "[3/4] Transfert et chargement sur les noeuds K8s ..." -ForegroundColor Yellow
    foreach ($NodeIp in @($MasterIp, $Worker1Ip, $Worker2Ip)) {
        Write-Host "      -> $NodeIp ..." -ForegroundColor DarkCyan
        scp $TarFile "${SshUser}@${NodeIp}:/tmp/pfe-frontend.tar"
        if ($LASTEXITCODE -ne 0) { throw "scp vers $NodeIp a echoue" }
        Invoke-Ssh $NodeIp "sudo ctr -n k8s.io images import /tmp/pfe-frontend.tar && rm /tmp/pfe-frontend.tar"
        if ($LASTEXITCODE -ne 0) { throw "Import image sur $NodeIp a echoue" }
        Write-Host "        OK" -ForegroundColor Green
    }
    Remove-Item $TarFile -ErrorAction SilentlyContinue
} else {
    Write-Host "[2/4] Sauvegarde/chargement ignores (--SkipLoad)" -ForegroundColor DarkGray
    Write-Host "[3/4] (ignore)" -ForegroundColor DarkGray
}

# ─── Etape 4 : Deploiement Kubernetes ────────────────────────────────────────
Write-Host "[4/4] Application des manifests Kubernetes ..." -ForegroundColor Yellow

$K8sDir = Join-Path $PSScriptRoot "..\k8s"
kubectl apply -f "$K8sDir\frontend-deployment.yaml"
if ($LASTEXITCODE -ne 0) { throw "kubectl apply frontend-deployment a echoue" }

kubectl apply -f "$K8sDir\frontend-service.yaml"
if ($LASTEXITCODE -ne 0) { throw "kubectl apply frontend-service a echoue" }

# Mise à jour du tag de l'image si différent de 1.0.0
kubectl -n pfe set image deployment/frontend "frontend=${ImageFull}"
if ($LASTEXITCODE -ne 0) { throw "kubectl set image a echoue" }

Write-Host "      Manifests appliques" -ForegroundColor Green

# ─── Attente du rollout ──────────────────────────────────────────────────────
Write-Host ""
Write-Host "Attente du rollout (max 120s) ..." -ForegroundColor Yellow
kubectl -n pfe rollout status deployment/frontend --timeout=120s
if ($LASTEXITCODE -ne 0) {
    Write-Host "ATTENTION : rollout timeout. Verifiez avec : kubectl -n pfe describe deployment/frontend" -ForegroundColor Red
} else {
    Write-Host ""
    Write-Host "=== Frontend deploye avec succes ===" -ForegroundColor Green
    Write-Host ""
    Write-Host "Acces application :" -ForegroundColor Cyan
    Write-Host "  http://${MasterIp}:30880   (master)"
    Write-Host "  http://${Worker1Ip}:30880  (worker1)"
    Write-Host "  http://${Worker2Ip}:30880  (worker2)"
    Write-Host ""
    Write-Host "Swagger backend :"
    Write-Host "  http://${MasterIp}:30808/swagger-ui.html"
    Write-Host ""
}

# ─── Statut final ────────────────────────────────────────────────────────────
kubectl -n pfe get pods -l app=frontend
