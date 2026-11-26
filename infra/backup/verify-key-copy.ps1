# HELIOS - verifie qu'une COPIE de la cle AES (offsite.key) est sur un support REELLEMENT SEPARE.
# Usage : powershell -File verify-key-copy.ps1 -Copy E:\cle-helios.key
# Contrat : la copie existe, est identique (SHA-256) a la cle, et se trouve sur un DISQUE PHYSIQUE different de celui de la cle (C:)
#           ET different de celui de la destination des sauvegardes (D:). Aucune valeur de cle n'est affichee.
param([Parameter(Mandatory=$true)][string]$Copy)
$key='C:\Users\houss\helios-state\offsite\offsite.key'; $destFile='C:\Users\houss\helios-state\offsite\dest.txt'
function DiskOf($path){ $l=(Split-Path -Qualifier (Resolve-Path $path)).TrimEnd(':'); (Get-Partition -DriveLetter $l -ErrorAction Stop).DiskNumber }
if(-not (Test-Path $key)){ Write-Host 'ECHEC : cle introuvable'; exit 2 }
if(-not (Test-Path $Copy)){ Write-Host "ECHEC : copie introuvable ($Copy)"; exit 2 }
$h1=(Get-FileHash $key -Algorithm SHA256).Hash; $h2=(Get-FileHash $Copy -Algorithm SHA256).Hash
$dk=DiskOf $key; $dc=DiskOf $Copy; $dd=$null; if(Test-Path $destFile){ $d=(Get-Content $destFile -Raw).Trim(); if($d -match '^[A-Za-z]:'){ $dd=DiskOf $d } }
$ok=$true
if($h1 -eq $h2){ Write-Host 'OK    copie identique (SHA-256)' } else { Write-Host 'ECHEC copie differente de la cle'; $ok=$false }
if($dc -ne $dk){ Write-Host "OK    disque different de celui de la cle (cle: disque $dk, copie: disque $dc)" } else { Write-Host "ECHEC meme disque physique que la cle (disque $dk)"; $ok=$false }
if($dd -ne $null){ if($dc -ne $dd){ Write-Host "OK    disque different de la destination des sauvegardes (destination: disque $dd)" } else { Write-Host "ECHEC meme disque que les sauvegardes (disque $dd) : cle et copies chiffrees ensemble"; $ok=$false } }
else { Write-Host 'INFO  destination des sauvegardes non locale ou non configuree : comparaison ignoree' }
if($ok){ Write-Host 'RESULTAT : OK - la cle est protegee sur un support separe'; exit 0 } else { Write-Host 'RESULTAT : ECART'; exit 1 }