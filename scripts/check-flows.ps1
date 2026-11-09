<#
.SYNOPSIS
  Fails if any "RFP - " flow in the target is off or missing. Read-only. Run after every deploy or import.

.DESCRIPTION
  The two hook flows were once found off with no record of why, and hooks fail open, so nothing else would show it
  (docs/RUNBOOK.md section 8). eval/run.ps1 runs this before a full run, so a run can't start with a flow off. To turn
  flows on, use apply-flows.ps1 in dev or activate-flows.ps1 in test.

.EXAMPLE
  ./scripts/check-flows.ps1 -Target test
#>
[CmdletBinding()]
param([ValidateSet('dev', 'test')][string]$Target = 'dev')

. (Join-Path $PSScriptRoot 'lib\Dataverse.ps1')
Connect-Dataverse -Url (Resolve-TargetUrl -Target $Target)
$expected = @(Get-ChildItem (Join-Path $script:RepoRoot 'solution\spec\flows') -Filter '*.json' | ForEach-Object { (Get-Content $_.FullName -Raw | ConvertFrom-Json -Depth 100).name })
$rows = @((Invoke-Dv -Path "workflows?`$select=name,statecode&`$filter=startswith(name,'RFP - ') and category eq 5").value)
$bad = @()
foreach ($name in $expected) {
    $row = $rows | Where-Object { $_.name -eq $name } | Select-Object -First 1
    if (-not $row) { $bad += "$name (missing)" } elseif ($row.statecode -ne 1) { $bad += "$name (off)" }
}
if ($bad) {
    Write-Host "Flows not ready in $Target`: $($bad -join ', ')"
    exit 1
}
Write-Host "All $($expected.Count) flows on in $Target"
