param(
    [string]$Kubeconfig = "investigation_outputs/2026-04-11-manual-steps-automation/kubeconfig-master",
    [string]$Namespace = "pfe",
    [string]$EnvFile = (Join-Path $PSScriptRoot "..\vm-config\monitoring-minio.env")
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

if (-not (Test-Path $EnvFile)) {
    throw "Missing $EnvFile. Copy monitoring-minio.env.example to monitoring-minio.env first."
}

$envMap = Read-EnvFile -Path $EnvFile
foreach ($key in @("MINIO_BACKUP_ACCESS_KEY", "MINIO_BACKUP_SECRET_KEY", "AWS_REGION")) {
    if (-not $envMap.ContainsKey($key)) {
        throw "Missing $key in $EnvFile"
    }
}

$tempFile = [System.IO.Path]::GetTempFileName()
try {
    & kubectl --kubeconfig $Kubeconfig -n $Namespace create secret generic cnpg-backup-s3 `
        --from-literal=ACCESS_KEY_ID=$($envMap["MINIO_BACKUP_ACCESS_KEY"]) `
        --from-literal=SECRET_ACCESS_KEY=$($envMap["MINIO_BACKUP_SECRET_KEY"]) `
        --from-literal=AWS_REGION=$($envMap["AWS_REGION"]) `
        --dry-run=client -o yaml | Set-Content -Path $tempFile

    & kubectl --kubeconfig $Kubeconfig apply -f $tempFile
}
finally {
    Remove-Item -Path $tempFile -ErrorAction SilentlyContinue
}
