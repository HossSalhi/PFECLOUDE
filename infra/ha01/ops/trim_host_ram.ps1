# HA-01 — réduit le working set des processus NON-VMware de l'hôte (EmptyWorkingSet). NON DESTRUCTIF : les pages passent en liste
# « standby » (comptée comme mémoire DISPONIBLE par Windows, réutilisable aussitôt) et reviennent à la demande ; aucun processus n'est
# arrêté. Les processus VMware (vmware-vmx, vmnat, vmware-authd...) ne sont JAMAIS touchés : réduire leur working set ferait paginer la
# mémoire des VM et provoquerait des gels (cf. constat des gels d'E/S de l'hôte).
# Effet transitoire (~10 min) : à relancer avant un préflight qui exige >= 2000 Mo de RAM hôte disponible.
# Usage : .\trim_host_ram.ps1
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class Ha01Ws { [DllImport("psapi.dll")] public static extern bool EmptyWorkingSet(IntPtr h); }
"@
$before = (Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory).AvailableMBytes
$n = 0
Get-Process | Where-Object { $_.Name -notmatch '^(vmware|vmnat|vmnetdhcp|vmms|vmcompute|System|Idle|Registry|Memory Compression|Secure System)' } | ForEach-Object {
  try { if ([Ha01Ws]::EmptyWorkingSet($_.Handle)) { $n++ } } catch { }
}
Start-Sleep -Milliseconds 800
$after = (Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory).AvailableMBytes
"RAM hote disponible : $before Mo -> $after Mo ($n processus non-VMware réduits)"
