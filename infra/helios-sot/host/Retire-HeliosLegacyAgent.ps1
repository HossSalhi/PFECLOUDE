<#
.SYNOPSIS
  HELIOS - retire l'agent legacy de mise sous tension :9999 (helios-vmpower-agent.py). DRY-RUN PAR DEFAUT.

.DESCRIPTION
  L'agent :9999 n'est PAS authentifie, ecoute sur toutes les interfaces et n'a plus aucun appelant : il est le dernier
  chemin de power-on hors politique (decision D9). Ce script, sans rien SUPPRIMER :
    1. arrete le processus qui ecoute sur 9999 s'il s'agit bien de helios-vmpower-agent ;
    2. neutralise sa ligne dans les deux copies de helios-agents-start.bat (dossier Demarrage et dossier personnel),
       apres sauvegarde horodatee ; la ligne de helios-host-relay.py est conservee ;
    3. renomme le script en  helios-vmpower-agent.py.RETIRE-20261105  (rien n'est supprime) ;
    4. cree la regle de blocage  "HELIOS-BLOCK-9999-ALL"  (entrant, TCP 9999, tous distants) ; la regle existante
       HELIOS-MAINT-BLOCK-9999 est conservee.
  Sans -Apply : rien n'est ecrit. -Apply exige une session ELEVEE.

  Retour arriere : restaurer les .bak-*, renommer le script a son nom d'origine, supprimer/desactiver la regle
  HELIOS-BLOCK-9999-ALL, relancer helios-agents-start.bat.
#>
[CmdletBinding()]
param(
    [string]$Script = 'C:\Users\houss\helios-vmpower-agent.py',
    [string[]]$Bats = @(
        (Join-Path ([Environment]::GetFolderPath('Startup')) 'helios-agents-start.bat'),
        'C:\Users\houss\helios-agents-start.bat'),
    [string]$BlockRule = 'HELIOS-BLOCK-9999-ALL',
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$stamp = '20261105'
$marker = "REM RETIRE-$stamp "
$pattern = 'helios-vmpower-agent\.py'

# Get-NetTCPConnection est PEU FIABLE sur ce poste (il n'affiche pas toujours l'agent alors qu'il repond) :
# on identifie les processus par leur ligne de commande et on teste la connexion TCP reelle.
function Test-Port($port) {
    try { $c = New-Object Net.Sockets.TcpClient; $iar = $c.BeginConnect('127.0.0.1', $port, $null, $null)
          $ok = $iar.AsyncWaitHandle.WaitOne(1500) -and $c.Connected; $c.Close(); return $ok } catch { return $false }
}
function Get-AgentProcs { Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match $pattern } }

Write-Host '== Etat actuel (lecture seule)'
$procs = @(Get-AgentProcs)
Write-Host ("  port 9999 : {0}" -f $(if (Test-Port 9999) { 'REPOND' } else { 'ne repond pas' }))
foreach ($pr in $procs) { Write-Host ("  processus agent : pid={0} cmd={1}" -f $pr.ProcessId, $pr.CommandLine) }
if (-not $procs) { Write-Host '  aucun processus agent' }
foreach ($b in $Bats) {
    if (Test-Path -LiteralPath $b) {
        $hits = @(Select-String -LiteralPath $b -Pattern $pattern | Where-Object { $_.Line -notmatch '^\s*REM ' })
        Write-Host ("  {0} : {1} ligne(s) active(s) vers l'agent" -f $b, $hits.Count)
    } else { Write-Host "  $b : absent" }
}
Write-Host ("  script : {0} -> {1}" -f $Script, $(if (Test-Path -LiteralPath $Script) { 'present' } else { 'absent' }))
Write-Host ("  regle de blocage {0} : {1}" -f $BlockRule, $(if (Get-NetFirewallRule -DisplayName $BlockRule -ErrorAction SilentlyContinue) { 'presente' } else { 'absente' }))

Write-Host ''
Write-Host '== Plan'
Write-Host '  1. arreter les processus dont la ligne de commande est helios-vmpower-agent.py, puis verifier que 9999 ne repond plus'
Write-Host "  2. neutraliser la ligne de l'agent dans chaque .bat (prefixe '$marker', sauvegarde .bak-<horodatage>)"
Write-Host ("  3. renommer {0} -> {0}.RETIRE-{1}" -f $Script, $stamp)
Write-Host ("  4. creer la regle de blocage '{0}' (entrant TCP 9999, tous distants)" -f $BlockRule)
Write-Host '  inchange : helios-host-relay.py, helios-relay-startup.vbs, HELIOS-MAINT-BLOCK-9999'

if (-not $Apply) {
    Write-Host ''
    Write-Host 'DRY-RUN : rien n''a ete ecrit. Relancer avec -Apply (session administrateur) apres autorisation.' -ForegroundColor Yellow
    exit 0
}
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Session non elevee : -Apply exige un PowerShell administrateur.' }

Write-Host ''
Write-Host '== Application'
foreach ($pr in $procs) {
    Stop-Process -Id $pr.ProcessId -Force
    Write-Host "  processus arrete : pid=$($pr.ProcessId)"
}
Start-Sleep -Seconds 2
if (Test-Port 9999) { throw 'le port 9999 repond encore apres l''arret : verifier manuellement (autre processus ?)' }
foreach ($b in $Bats) {
    if (-not (Test-Path -LiteralPath $b)) { continue }
    $lines = Get-Content -LiteralPath $b
    if (-not ($lines | Where-Object { $_ -match $pattern -and $_ -notmatch '^\s*REM ' })) { Write-Host "  $b : deja neutralise"; continue }
    Copy-Item -LiteralPath $b -Destination ("{0}.bak-{1}" -f $b, (Get-Date -Format 'yyyyMMdd-HHmmss'))
    $new = $lines | ForEach-Object { if ($_ -match $pattern -and $_ -notmatch '^\s*REM ') { $marker + $_ } else { $_ } }
    Set-Content -LiteralPath $b -Value $new -Encoding ASCII
    Write-Host "  $b : ligne de l'agent neutralisee"
}
if (Test-Path -LiteralPath $Script) {
    $target = "$Script.RETIRE-$stamp"
    if (Test-Path -LiteralPath $target) { Write-Host "  $target existe deja : script non renomme" -ForegroundColor Yellow }
    else { Rename-Item -LiteralPath $Script -NewName (Split-Path $target -Leaf); Write-Host "  script renomme : $target" }
}
if (-not (Get-NetFirewallRule -DisplayName $BlockRule -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $BlockRule -Direction Inbound -Action Block -Protocol TCP -LocalPort 9999 -Profile Any -Enabled True | Out-Null
    Write-Host "  regle creee : $BlockRule"
}
Write-Host 'Termine. Verifier : aucun processus n''ecoute sur 9999 ; le relais (helios-host-relay.py) est inchange.'
