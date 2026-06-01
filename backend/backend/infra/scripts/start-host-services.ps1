param(
    [string]$DockerExe = "C:\Program Files\Docker\Docker\resources\bin\docker.exe",
    [string]$HostServicesDir = (Join-Path $PSScriptRoot "..\host-services")
)

$ErrorActionPreference = "Stop"

function Read-EnvFile {
    param([string]$Path)

    $result = @{}
    foreach ($line in Get-Content -Path $Path) {
        if ([string]::IsNullOrWhiteSpace($line) -or $line.TrimStart().StartsWith("#")) {
            continue
        }
        $parts = $line -split "=", 2
        if ($parts.Count -eq 2) {
            $result[$parts[0].Trim()] = $parts[1].Trim()
        }
    }
    return $result
}

if (-not (Test-Path $DockerExe)) {
    throw "Docker executable not found at $DockerExe"
}

$resolvedHostServicesDir = (Resolve-Path $HostServicesDir).Path
$envFile = Join-Path $resolvedHostServicesDir ".env"
$composeFile = Join-Path $resolvedHostServicesDir "docker-compose.yml"
$camundaOverride = Join-Path $resolvedHostServicesDir "camunda.override.yaml"

if (-not (Test-Path $envFile)) {
    throw "Missing $envFile. Copy .env.example to .env first."
}

$envMap = Read-EnvFile -Path $envFile
$camundaVersion = if ($envMap.ContainsKey("CAMUNDA_DOCKER_COMPOSE_VERSION")) { $envMap["CAMUNDA_DOCKER_COMPOSE_VERSION"] } else { "8.8" }
$cacheDir = Join-Path $resolvedHostServicesDir ".cache\camunda-$camundaVersion"
$zipPath = Join-Path $cacheDir "docker-compose-$camundaVersion.zip"
$extractDir = Join-Path $cacheDir "extract"
$releaseUrl = "https://github.com/camunda/camunda-distributions/releases/download/docker-compose-$camundaVersion/docker-compose-$camundaVersion.zip"

New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null

Write-Host "Starting MinIO host services..."
& $DockerExe compose --env-file $envFile -f $composeFile up -d minio

Write-Host "Bootstrapping MinIO backup bucket and service account..."
$minioNetwork = (& $DockerExe inspect pfe-host-services-minio-1 --format "{{range `$k,`$v := .NetworkSettings.Networks}}{{`$k}}{{end}}").Trim()
if ([string]::IsNullOrWhiteSpace($minioNetwork)) {
    throw "Unable to determine the Docker network for pfe-host-services-minio-1"
}

$bootstrapCommand = @"
mc alias set local http://pfe-host-services-minio-1:9000 "$($envMap["MINIO_ROOT_USER"])" "$($envMap["MINIO_ROOT_PASSWORD"])" >/dev/null &&
mc mb --ignore-existing local/$($envMap["MINIO_BACKUP_BUCKET"]) &&
(mc admin user info local "$($envMap["MINIO_BACKUP_ACCESS_KEY"])" >/dev/null 2>&1 || mc admin user add local "$($envMap["MINIO_BACKUP_ACCESS_KEY"])" "$($envMap["MINIO_BACKUP_SECRET_KEY"])") &&
mc admin policy attach local readwrite --user "$($envMap["MINIO_BACKUP_ACCESS_KEY"])"
"@ -replace "`r?`n", " "

& $DockerExe run --rm --network $minioNetwork --entrypoint /bin/sh minio/mc:latest -lc $bootstrapCommand

if (-not (Test-Path $zipPath)) {
    Write-Host "Downloading official Camunda Docker Compose bundle $camundaVersion..."
    Invoke-WebRequest -Uri $releaseUrl -OutFile $zipPath
}

if (-not (Test-Path $extractDir)) {
    Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force
}

$coreCompose = Get-ChildItem -Path $extractDir -Recurse -Filter "docker-compose-core.yaml" | Select-Object -First 1
if (-not $coreCompose) {
    $coreCompose = Get-ChildItem -Path $extractDir -Recurse -Filter "docker-compose.yaml" | Select-Object -First 1
}
if (-not $coreCompose) {
    throw "Unable to locate a Camunda docker-compose file under $extractDir"
}

Write-Host "Starting official Camunda Core stack from $($coreCompose.FullName)..."
Push-Location $coreCompose.DirectoryName
try {
    & $DockerExe compose -f $coreCompose.FullName -f $camundaOverride up -d elasticsearch orchestration
}
finally {
    Pop-Location
}

Write-Host "Host services started."
