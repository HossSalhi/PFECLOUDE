#requires -Version 5.1
#requires -RunAsAdministrator
[CmdletBinding()]
param(
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
)

$ErrorActionPreference = 'Stop'
$NodeIp = '192.168.148.158'
$BindIp = '192.168.148.1'
$Port = 19158
$TaskName = 'HELIOS-DISK-Host-Agent'
$RuleAllow = 'HELIOS DISK Host Agent 19158 - allow .158'
$RuleBlock = 'HELIOS DISK Host Agent 19158 - block other callers'
$Agent = Join-Path $PSScriptRoot 'helios_disk_host_agent.py'
$DiskTool = Join-Path $PSScriptRoot 'helios_disk_test.py'
$E2ETool = Join-Path $PSScriptRoot 'helios_disk_e2e.py'
$BridgeSoT = Join-Path $RepositoryRoot 'infra\helios-sot\bridge\bridge.py'
$StateDir = Join-Path $env:ProgramData 'HELIOS'
$KeyPath = Join-Path $StateDir 'disk-host-agent.key'
$RemoteDir = '/etc/helios-diskfill'
$RemoteKey = "$RemoteDir/host-agent.key"
$RemoteTmp = '/tmp/helios-disk-host-agent-' + [Guid]::NewGuid().ToString('N')

function Assert-NativeSuccess([string]$Action) {
    if ($LASTEXITCODE -ne 0) { throw "$Action a echoue (rc=$LASTEXITCODE)." }
}

if (-not (Test-Path -LiteralPath $Agent -PathType Leaf) -or
    -not (Test-Path -LiteralPath $DiskTool -PathType Leaf) -or
    -not (Test-Path -LiteralPath $E2ETool -PathType Leaf) -or
    -not (Test-Path -LiteralPath $BridgeSoT -PathType Leaf)) {
    throw 'Sources DISK manquantes : agent, helios_disk_test.py, helios_disk_e2e.py et infra/helios-sot/bridge/bridge.py sont requis.'
}

$PythonCommand = Get-Command python.exe -ErrorAction Stop
$Python = $PythonCommand.Source
$SshCommand = Get-Command ssh.exe -ErrorAction Stop
$Ssh = $SshCommand.Source
$ScpCommand = Get-Command scp.exe -ErrorAction Stop
$Scp = $ScpCommand.Source

$IpPresent = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $BindIp -ErrorAction SilentlyContinue
if (-not $IpPresent) { throw "L'adresse $BindIp n'est pas configuree sur Windows ; aucun bind sur 0.0.0.0 ne sera utilise." }
if (Get-NetTCPConnection -State Listen -LocalAddress $BindIp -LocalPort $Port -ErrorAction SilentlyContinue) {
    throw "Le port $Port est deja utilise sur $BindIp. Aucun processus ne sera arrete."
}
if (Test-Path -LiteralPath $KeyPath) { throw "La cle locale existe deja ($KeyPath). Pas d'ecrasement/rotation automatique." }
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { throw "La tache $TaskName existe deja. Pas de remplacement automatique." }
if (Get-NetFirewallRule -DisplayName $RuleAllow -ErrorAction SilentlyContinue) { throw "La regle pare-feu $RuleAllow existe deja. Pas de remplacement automatique." }
if (Get-NetFirewallRule -DisplayName $RuleBlock -ErrorAction SilentlyContinue) { throw "La regle pare-feu $RuleBlock existe deja. Pas de remplacement automatique." }

# Verifie l'identite SSH et l'absence de cle distante avant de produire le secret.
& $Ssh -o BatchMode=yes -o ConnectTimeout=7 "kube@$NodeIp" "sudo -n test ! -e $RemoteKey"
Assert-NativeSuccess 'Verification SSH/sudo et absence de cle distante'

$bytes = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
$token = (($bytes | ForEach-Object { $_.ToString('x2') }) -join '')

New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
$userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $StateDir /inheritance:r
Assert-NativeSuccess 'Protection ACL du dossier ProgramData'
& icacls.exe $StateDir /grant:r "*$($userSid):(OI)(CI)M" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F'
Assert-NativeSuccess 'ACL du dossier ProgramData'
[IO.File]::WriteAllText($KeyPath, $token, [Text.Encoding]::ASCII)
& icacls.exe $KeyPath /inheritance:r
Assert-NativeSuccess 'Protection ACL de la cle locale'
& icacls.exe $KeyPath /grant:r "*$($userSid):(R)" '*S-1-5-18:(F)' '*S-1-5-32-544:(F)'
Assert-NativeSuccess 'ACL de la cle locale'

# Provisionnement de la meme cle sur .158 via SSH chiffre ; le secret ne figure pas dans la ligne de commande distante.
$tmpLocal = Join-Path $env:TEMP ('helios-disk-host-agent-' + [Guid]::NewGuid().ToString('N') + '.key')
try {
    [IO.File]::WriteAllText($tmpLocal, $token, [Text.Encoding]::ASCII)
    & $Scp -q -o BatchMode=yes -o ConnectTimeout=7 $tmpLocal "kube@${NodeIp}:$RemoteTmp"
    Assert-NativeSuccess 'Transfert SSH temporaire de la cle vers .158'
    $installCmd = "sudo -n install -d -o root -g root -m 0700 $RemoteDir && sudo -n install -o root -g root -m 0600 -- $RemoteTmp $RemoteKey && rm -f -- $RemoteTmp"
    & $Ssh -o BatchMode=yes -o ConnectTimeout=7 "kube@$NodeIp" $installCmd
    Assert-NativeSuccess 'Installation de la cle root:root 0600 sur .158'
    & $Ssh -o BatchMode=yes -o ConnectTimeout=7 "kube@$NodeIp" "sudo -n stat -c '%U:%G:%a' $RemoteKey"
    Assert-NativeSuccess 'Verification des permissions de la cle sur .158'
} finally {
    Remove-Item -LiteralPath $tmpLocal -Force -ErrorAction SilentlyContinue
    & $Ssh -o BatchMode=yes -o ConnectTimeout=5 "kube@$NodeIp" "rm -f -- $RemoteTmp" 2>$null | Out-Null
}

# Regles IPv4 strictes : allow .158 et block toutes les autres IPv4 ; aucune modification du port 9099.
New-NetFirewallRule -DisplayName $RuleAllow -Direction Inbound -Action Allow -Protocol TCP `
    -LocalAddress $BindIp -LocalPort $Port -RemoteAddress $NodeIp -Profile Private -Enabled True | Out-Null
New-NetFirewallRule -DisplayName $RuleBlock -Direction Inbound -Action Block -Protocol TCP `
    -LocalAddress $BindIp -LocalPort $Port `
    -RemoteAddress @('0.0.0.0-192.168.148.157','192.168.148.159-255.255.255.255') `
    -Profile Private -Enabled True | Out-Null

$identity = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute $Python `
    -Argument "-I -B -u `"$Agent`" --serve" -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description 'HELIOS DISK host preflight agent; read-only; caller .158 only; VM_DOWN watchdog :9099 remains independent.' | Out-Null
Start-ScheduledTask -TaskName $TaskName

$deadline = (Get-Date).AddSeconds(15)
$listener = $null
while ((Get-Date) -lt $deadline) {
    $listener = Get-NetTCPConnection -State Listen -LocalAddress $BindIp -LocalPort $Port -ErrorAction SilentlyContinue
    if ($listener) { break }
    Start-Sleep -Milliseconds 500
}
if (-not $listener) {
    throw "L'agent n'ecoute pas sur $BindIp`:$Port. La tache est enregistree mais le service DISK restera en refus ferme. Examiner Get-ScheduledTaskInfo -TaskName '$TaskName'."
}

Write-Host ''
Write-Host 'AGENT WINDOWS DISK INSTALLE.' -ForegroundColor Green
Write-Host "Ecoute : $BindIp`:$Port ; appelant autorise : $NodeIp"
Write-Host "Tache : $TaskName (au logon de $identity ; relance limitee en cas d'echec)"
Write-Host "Cle Linux : $RemoteKey (root:root 0600) ; cle Windows protegee par ACL dans $StateDir"
Write-Host 'Le watchdog VM_DOWN :9099 et ses regles n ont pas ete modifies.'
Write-Host 'Aucun test de remplissage n a ete lance. Le service Linux doit ensuite etre deploye/verifie avant le premier start.'
