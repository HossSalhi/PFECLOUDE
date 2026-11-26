<#
.SYNOPSIS
  HELIOS - ouvre le port 9099 du watchdog hote aux SEULS appelants autorises (les 3 noeuds .153/.155/.158)
  ET le ferme a toute autre adresse. DRY-RUN PAR DEFAUT.

.DESCRIPTION
  Politique VM_DOWN (decision D6, etendue le 5 nov. 2026 a .153 comme APPELANT seulement) : le BACKEND est le seul
  appelant autorise de POST /restart ; ses pods (2, anti-affinite stricte) sortent vers l'hote avec l'adresse de leur
  noeud (.153, .155 ou .158, Calico natOutgoing). .153 etait autorisee seulement a APPELER (k8s-master = DENY_RESTART) ;
  depuis la consigne du 17 nov. 2026 elle est aussi CIBLE AUTO (k8s-master = AUTO). Ce pare-feu ne decide d'aucune cible :
  seule la table de politique du watchdog (VM_POLICY) le fait ; les APPELANTS restent .153/.155/.158 (regles inchangees).

  CONSTAT du 6 nov. 2026 (lecture seule) : Windows a cree automatiquement des regles « python.exe » / « pythonw.exe »
  (profil Public, TOUTES adresses, TOUS ports) ; les regles ALLOW s'additionnent, donc l'ancienne regle « Bridge .161
  uniquement » etait inoperante : .160, .161 et .162 atteignaient 9099 (le watchdog les refusait en 403, mais /health
  etait lisible). Un BLOCK l'emporte sur tout ALLOW : ce script ajoute donc une regle BLOCK pour tout le monde SAUF les
  appelants. Ce script :
    1. cree la regle ALLOW "HELIOS Watchdog 9099 - backend .153/.155/.158" (entrant, TCP 9099, adresse locale
       192.168.148.1, distants .153, .155 et .158, tous profils). Non liee a un programme : un changement
       d'interpreteur Python ne coupera pas silencieusement l'acces ;
    2. cree la regle BLOCK "HELIOS Watchdog 9099 - BLOCK appelants non autorises" (entrant, TCP 9099, adresse locale
       192.168.148.1, distants = TOUTES les adresses IPv4 SAUF les appelants, calculees par ce script) ;
    3. DESACTIVE (ne supprime pas) "HELIOS Watchdog 9099 - Bridge .161 uniquement" : le Bridge n'appelle plus le
       watchdog. La regle reste en place pour un retour arriere (Enable-NetFirewallRule).
  Aucune autre regle n'est touchee. Necessite une session ELEVEE pour -Apply. Sans -Apply : rien n'est ecrit.

.EXAMPLE
  .\Set-HeliosWatchdogFirewall.ps1            # dry-run
  .\Set-HeliosWatchdogFirewall.ps1 -Apply     # applique (session administrateur)
#>
[CmdletBinding()]
param(
    [string[]]$Callers = @('192.168.148.153', '192.168.148.155', '192.168.148.158'),
    [string]$LocalAddress = '192.168.148.1',
    [int]$Port = 9099,
    [string]$NewRule = 'HELIOS Watchdog 9099 - backend .153/.155/.158',
    [string]$BlockRule = 'HELIOS Watchdog 9099 - BLOCK appelants non autorises',
    [string]$OldRule = 'HELIOS Watchdog 9099 - Bridge .161 uniquement',
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'

function ConvertTo-UInt64Ip([string]$Ip) {
    $b = [System.Net.IPAddress]::Parse($Ip).GetAddressBytes()
    return [uint64]($b[0] * 16777216 + $b[1] * 65536 + $b[2] * 256 + $b[3])
}

function ConvertFrom-UInt64Ip([uint64]$N) {
    return '{0}.{1}.{2}.{3}' -f [int](($N -shr 24) -band 255), [int](($N -shr 16) -band 255), [int](($N -shr 8) -band 255), [int]($N -band 255)
}

# Complement IPv4 d'un ensemble d'adresses : liste de « debut-fin » (ou d'adresses seules) couvrant TOUT l'espace
# 0.0.0.0-255.255.255.255 SAUF les adresses autorisees.
function Get-ComplementRanges([string[]]$Allowed) {
    $ints = @($Allowed | ForEach-Object { ConvertTo-UInt64Ip $_ } | Sort-Object -Unique)
    $max = [uint64]4294967295
    $out = New-Object System.Collections.Generic.List[string]
    $start = [uint64]0
    foreach ($a in $ints) {
        if ($a -gt $start) {
            $end = [uint64]($a - 1)
            if ($end -eq $start) { $out.Add((ConvertFrom-UInt64Ip $start)) } else { $out.Add((ConvertFrom-UInt64Ip $start) + '-' + (ConvertFrom-UInt64Ip $end)) }
        }
        $start = [uint64]($a + 1)
    }
    if ($start -le $max) {
        if ($start -eq $max) { $out.Add((ConvertFrom-UInt64Ip $start)) } else { $out.Add((ConvertFrom-UInt64Ip $start) + '-' + (ConvertFrom-UInt64Ip $max)) }
    }
    return $out.ToArray()
}

function Show-Rule($name) {
    $r = Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
    if (-not $r) { Write-Host "  [absente] $name"; return }
    $pf = $r | Get-NetFirewallPortFilter
    $af = $r | Get-NetFirewallAddressFilter
    Write-Host ("  [{0}] {1} : {2} {3} {4}:{5} <- {6}" -f $(if ($r.Enabled -eq 'True') { 'active' } else { 'inactive' }), $name, $r.Direction, $r.Action, ($af.LocalAddress -join ','), ($pf.LocalPort -join ','), ($af.RemoteAddress -join ','))
}

$blocked = Get-ComplementRanges $Callers

Write-Host '== Etat actuel (lecture seule)'
Show-Rule $OldRule
Show-Rule $NewRule
Show-Rule $BlockRule

Write-Host ''
Write-Host '== Plan'
Write-Host ("  1. creer   '{0}' : Inbound Allow TCP {1}, local {2}, distants {3}, profil Any, sans programme" -f $NewRule, $Port, $LocalAddress, ($Callers -join ' + '))
Write-Host ("  2. creer   '{0}' : Inbound Block TCP {1}, local {2}, distants (tout SAUF les appelants) = {3}" -f $BlockRule, $Port, $LocalAddress, ($blocked -join ' ; '))
Write-Host ("  3. desactiver '{0}' (conservee pour retour arriere)" -f $OldRule)
Write-Host '  inchange : HELIOS-MAINT-BLOCK-9099, HELIOS-MAINT-BLOCK-9999, HELIOS-Backend-Bridge161-18081, regles automatiques python.exe / pythonw.exe (neutralisees pour 9099 par le BLOCK)'
Write-Host ("  retour arriere : Disable-NetFirewallRule -DisplayName '{0}','{1}' ; Enable-NetFirewallRule -DisplayName '{2}'" -f $NewRule, $BlockRule, $OldRule)

if (-not $Apply) {
    Write-Host ''
    Write-Host 'DRY-RUN : rien n''a ete ecrit. Relancer avec -Apply (session administrateur) apres autorisation.' -ForegroundColor Yellow
    exit 0
}

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Session non elevee : -Apply exige un PowerShell administrateur.' }

Write-Host ''
Write-Host '== Application'
if (-not (Get-NetFirewallRule -DisplayName $NewRule -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $NewRule -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port `
        -LocalAddress $LocalAddress -RemoteAddress $Callers -Profile Any -Enabled True | Out-Null
    Write-Host "  regle creee : $NewRule"
} else {
    Write-Host "  regle deja presente : $NewRule (inchangee)"
}
if (-not (Get-NetFirewallRule -DisplayName $BlockRule -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $BlockRule -Direction Inbound -Action Block -Protocol TCP -LocalPort $Port `
        -LocalAddress $LocalAddress -RemoteAddress $blocked -Profile Any -Enabled True | Out-Null
    Write-Host "  regle creee : $BlockRule"
} else {
    Write-Host "  regle deja presente : $BlockRule (inchangee)"
}
if (Get-NetFirewallRule -DisplayName $OldRule -ErrorAction SilentlyContinue) {
    Disable-NetFirewallRule -DisplayName $OldRule
    Write-Host "  regle desactivee : $OldRule"
}
Write-Host ''
Show-Rule $OldRule
Show-Rule $NewRule
Show-Rule $BlockRule
Write-Host 'A verifier ensuite : depuis .153, .155 puis .158 -> curl -s http://192.168.148.1:9099/health = 200 ; depuis .160, .161 et .162 -> connexion REFUSEE / expiree.'
