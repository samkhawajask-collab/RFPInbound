<#
.SYNOPSIS
  Dev/test only: confirms every open OCR verification of one RFP and resumes the pipeline, so a scoring run
  needs no human (ADR-0025, ADR-0009 amendment 6).

.DESCRIPTION
  Waits until stage 2 has written all its OCR-LOW-CONFIDENCE flags, cancels the "RFP - Verify OCR Requirement"
  card runs for them, marks each flag resolved as confirmed, and sets the RFP back to Running so the pipeline
  continues at stage 3.

  It only ever CONFIRMS the OCR text. It never corrects a requirement, so no answer-key text can reach
  Dataverse (hard rule 1); misreads stay as read and are scored by the harness.

.EXAMPLE
  ./scripts/complete-verifications.ps1 -Key D
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string]$Key,
    [ValidateSet('dev', 'test')][string]$Target = 'dev',
    [int]$WaitSeconds = 180
)

. (Join-Path $PSScriptRoot 'lib\Dataverse.ps1')
$url = Resolve-TargetUrl -Target $Target
Connect-Dataverse -Url $url

$OCR_FLAG = 431200153
$AWAITING = 431200172
$RUNNING = 431200171

$rfp = (Invoke-Dv -Path "hrs_rfps?`$select=hrs_rfpid&`$filter=hrs_rfpkey eq '$Key'").value | Select-Object -First 1
if (-not $rfp) { throw "No hrs_rfp with key $Key" }
$id = $rfp.hrs_rfpid

# Stage 2 sets Awaiting before it writes the flags, then logs "Waiting for N OCR verification(s)." once they exist.
$expected = $null
$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    $log = (Invoke-Dv -Path "hrs_runlogs?`$select=hrs_errordetail&`$filter=_hrs_rfp_value eq $id and hrs_stage eq 2&`$orderby=createdon desc&`$top=1").value |
        Select-Object -First 1
    if ($log.hrs_errordetail -match 'Waiting for (\d+) OCR verification') { $expected = [int]$Matches[1]; break }
    Start-Sleep -Seconds 10
}
if ($null -eq $expected) { Write-Host "RFP $Key has no OCR verifications waiting."; return }

$open = @((Invoke-Dv -Path "hrs_flags?`$select=hrs_flagid&`$filter=_hrs_rfp_value eq $id and hrs_flagtype eq $OCR_FLAG and hrs_resolvedon eq null").value |
    ForEach-Object { $_.hrs_flagid })
if ($open) {
    # Card runs start from the flag-created trigger, a little after the flags; cancel until each one is caught.
    $envId = Get-PowerPlatformEnvId -Url $url
    $stopped = 0
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    do {
        $stopped += Stop-FlowRuns -EnvId $envId -FlowName 'RFP - Verify OCR Requirement' -RecordColumn hrs_flagid -RecordIds $open
        if ($stopped -ge $open.Count) { break }
        Start-Sleep -Seconds 20
    } while ((Get-Date) -lt $deadline)
    foreach ($f in $open) {
        if ($PSCmdlet.ShouldProcess("hrs_flags($f)", 'resolve as confirmed')) {
            Invoke-Dv -Method PATCH -Path "hrs_flags($f)" -Body @{
                hrs_resolvedon = (Get-Date).ToUniversalTime().ToString('o'); hrs_resolvedby = 'complete-verifications.ps1 (OCR text confirmed, not corrected)'
            } | Out-Null
        }
    }
    Write-Host "$($open.Count) of $expected OCR verification(s) confirmed; $stopped card run(s) cancelled"
}

$now = Invoke-Dv -Path "hrs_rfps($id)?`$select=hrs_stage,hrs_status"
if ($now.hrs_stage -ne 2 -or $now.hrs_status -ne $AWAITING) {
    Write-Host "RFP $Key already resumed (stage $($now.hrs_stage))."
    return
}
if ($PSCmdlet.ShouldProcess("hrs_rfps($id)", 'Running')) {
    Invoke-Dv -Method PATCH -Path "hrs_rfps($id)" -Body @{ hrs_status = $RUNNING } | Out-Null
}
Write-Host "RFP $Key resumed."
