<#
.SYNOPSIS
  Packs solution/src into solution/build/RFPFactory.zip (hard rule 2: source is authoritative).

.EXAMPLE
  ./scripts/pack.ps1
  ./scripts/pack.ps1 -PackageType Managed
#>
[CmdletBinding()]
param(
    [ValidateSet('Unmanaged', 'Managed')][string]$PackageType = 'Unmanaged'
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$build = Join-Path $root 'solution\build'
New-Item -ItemType Directory -Force $build | Out-Null
$zip = Join-Path $build $(if ($PackageType -eq 'Managed') { 'RFPFactory_managed.zip' } else { 'RFPFactory.zip' })

pac solution pack --zipfile $zip --folder (Join-Path $root 'solution\src') --packagetype $PackageType
if ($LASTEXITCODE -ne 0) { throw 'pac solution pack failed' }
Write-Host "Packed $zip"
