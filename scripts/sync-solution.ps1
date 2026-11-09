<#
.SYNOPSIS
  Reconciles solution/src with dev: export the RFPFactory solution (unmanaged) and unpack it.

.DESCRIPTION
  Run after scripts/apply-schema.ps1 or after any change made in the maker or Copilot Studio
  portal, so git stays the source of truth (CLAUDE.md, ADR-0011, ADR-0014).

.EXAMPLE
  ./scripts/sync-solution.ps1
#>
[CmdletBinding()]
param()

. (Join-Path $PSScriptRoot 'lib\Dataverse.ps1')

$url = Resolve-TargetUrl -Target dev
$build = Join-Path $script:RepoRoot 'solution\build'
$src = Join-Path $script:RepoRoot 'solution\src'
$zip = Join-Path $build 'RFPFactory_export.zip'
New-Item -ItemType Directory -Force $build | Out-Null

Remove-Item $zip -ErrorAction SilentlyContinue
$exportOut = pac solution export --name RFPFactory --path $zip --managed false --overwrite --async --max-async-wait-time 90 --environment $url 2>&1
$exportOut | Write-Host
# pac can print an error and still exit 0, so check for the file as well.
if ($LASTEXITCODE -ne 0 -or ($exportOut -match '^Error:') -or -not (Test-Path $zip)) { throw 'pac solution export failed' }

# The fresh zip can stay locked briefly (antivirus scan, pac releasing its handle), and pac
# unpack can report an error yet exit 0 - so retry, then verify the unpacked output exists.
$solutionXml = Join-Path $src 'Other\Solution.xml'
for ($attempt = 1; $attempt -le 5; $attempt++) {
    $out = pac solution unpack --zipfile $zip --folder $src --packagetype Unmanaged --allowDelete true --allowWrite true --clobber true 2>&1
    $out | Write-Host
    if ($LASTEXITCODE -eq 0 -and -not ($out -match '^Error:') -and (Test-Path $solutionXml)) { break }
    if ($attempt -eq 5) { throw 'pac solution unpack failed' }
    Start-Sleep -Seconds (5 * $attempt)
}
Write-Host "solution/src reconciled from $url"
