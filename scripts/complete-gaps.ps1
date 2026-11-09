<#
.SYNOPSIS
  Dev/test only: closes every open gap of one RFP as "Cannot answer" and resumes the pipeline, so a scoring
  run needs no human (ADR-0020).

.DESCRIPTION
  Cancels the open "RFP - Route Gap To SME" runs for the RFP's Routed gaps, sets those gaps to Cannot answer,
  and sets the RFP back to Running so the pipeline continues at stage 8. Scoring does not depend on SME
  answer text: a gap's compliance stays Gap either way, and gap routing is scored on the routed SME.

.EXAMPLE
  ./scripts/complete-gaps.ps1 -Key A
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string]$Key,
    [ValidateSet('dev', 'test')][string]$Target = 'dev'
)

. (Join-Path $PSScriptRoot 'lib\Dataverse.ps1')
$url = Resolve-TargetUrl -Target $Target
Connect-Dataverse -Url $url

$rfp = (Invoke-Dv -Path "hrs_rfps?`$select=hrs_rfpid&`$filter=hrs_rfpkey eq '$Key'").value | Select-Object -First 1
if (-not $rfp) { throw "No hrs_rfp with key $Key" }
$open = @((Invoke-Dv -Path "hrs_gaps?`$select=hrs_gapid&`$filter=hrs_requirement/_hrs_rfp_value eq $($rfp.hrs_rfpid) and hrs_smestatus eq 431200190").value | ForEach-Object { $_.hrs_gapid })
if ($open) {
    $stopped = Stop-FlowRuns -EnvId (Get-PowerPlatformEnvId -Url $url) -FlowName 'RFP - Route Gap To SME' -RecordColumn hrs_gapid -RecordIds $open
    foreach ($g in $open) {
        if ($PSCmdlet.ShouldProcess("hrs_gaps($g)", 'Cannot answer')) { Invoke-Dv -Method PATCH -Path "hrs_gaps($g)" -Body @{ hrs_smestatus = 431200192 } | Out-Null }
    }
    Write-Host "$($open.Count) gap(s) closed as Cannot answer; $stopped card run(s) cancelled"
}
# Closing the last gap can resume the pipeline by itself; setting Running after it has moved on would
# overwrite the stage 9 Awaiting human status, so resume only a run still waiting at stage 7.
$now = Invoke-Dv -Path "hrs_rfps($($rfp.hrs_rfpid))?`$select=hrs_stage,hrs_status"
if ($now.hrs_stage -ne 7 -or $now.hrs_status -ne 431200172) {
    Write-Host "RFP $Key already resumed by the pipeline (stage $($now.hrs_stage))."
    return
}
if ($PSCmdlet.ShouldProcess("hrs_rfps($($rfp.hrs_rfpid))", 'Running')) {
    Invoke-Dv -Method PATCH -Path "hrs_rfps($($rfp.hrs_rfpid))" -Body @{ hrs_status = 431200171 } | Out-Null
}
Write-Host "RFP $Key resumed."
