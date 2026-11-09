<#
.SYNOPSIS
  Dev/test only: rewinds one RFP's run so the pipeline reruns from stage N (ADR-0018, ADR-0026).

.DESCRIPTION
  The same rule the pipeline's failure handler uses: every row created for the RFP since stage N's latest pass
  started is removed (approvals, clarifications, gaps, responses, flags, requirements, document blocks, and Draft
  library proposals from its gaps, PROP-<key>-*). That undoes stages N..9. Stage 4 clears the relation columns
  itself; compliance values and drafts are rewritten by stages 6 and later.

  Card flow runs started by the gaps and approvals being removed are cancelled first (their Teams cards stay
  visible but no longer accept answers - Q9). From stage 3 or earlier, the run restarts at stage 2 with
  hrs_currentrevision reset to 0, so stage 3 applies every addendum again to freshly extracted requirements.

  Then hrs_stage = N-1 and the status is set to Running, so RFP - Run Pipeline resumes at stage N. Run-log rows
  are kept as the audit trail. Reference data is never touched.

.EXAMPLE
  ./scripts/rewind-run.ps1 -Key A -FromStage 5
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string]$Key,
    [Parameter(Mandatory)][ValidateRange(1, 9)][int]$FromStage,
    [ValidateSet('dev', 'test')][string]$Target = 'dev'
)

. (Join-Path $PSScriptRoot 'lib\Dataverse.ps1')
$url = Resolve-TargetUrl -Target $Target
Connect-Dataverse -Url $url
$envId = Get-PowerPlatformEnvId -Url $url

$rfp = (Invoke-Dv -Path "hrs_rfps?`$select=hrs_rfpid,hrs_stage&`$filter=hrs_rfpkey eq '$Key'").value | Select-Object -First 1
if (-not $rfp) { throw "No hrs_rfp with key $Key" }
$id = $rfp.hrs_rfpid
if ($FromStage -eq 3) { Write-Host 'Stage 3 reruns from stage 2: addenda are applied to freshly extracted requirements.'; $FromStage = 2 }

$pass = (Invoke-Dv -Path "hrs_runlogs?`$select=hrs_startedon&`$filter=_hrs_rfp_value eq $id and hrs_stage eq $FromStage&`$orderby=createdon desc&`$top=1").value | Select-Object -First 1
if (-not $pass) { throw "RFP $Key has no run-log row for stage $FromStage; nothing to rewind." }
$since = ([datetime]$pass.hrs_startedon).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
Write-Host "Removing rows created since stage $FromStage started at $since"

function Get-Ids([string]$set, [string]$idColumn, [string]$filter) {
    @((Invoke-Dv -Path "$set`?`$select=$idColumn&`$filter=$([uri]::EscapeDataString("$filter and createdon ge $since"))").value | ForEach-Object { $_.$idColumn })
}
$approvals = @(Get-Ids 'hrs_approvals' 'hrs_approvalid' "_hrs_rfp_value eq $id")
if ($approvals) { Write-Host "  approval card runs cancelled: $(Stop-FlowRuns -EnvId $envId -FlowName 'RFP - Request Approval' -RecordColumn hrs_approvalid -RecordIds $approvals)" }
$gaps = @(Get-Ids 'hrs_gaps' 'hrs_gapid' "hrs_requirement/_hrs_rfp_value eq $id")
if ($gaps) { Write-Host "  gap card runs cancelled: $(Stop-FlowRuns -EnvId $envId -FlowName 'RFP - Route Gap To SME' -RecordColumn hrs_gapid -RecordIds $gaps)" }

# Dependents first; the same tables and order as created_since_tables in tools/flowgen/build.py.
$tables = @(
    @('hrs_approvals', 'hrs_approvalid', "_hrs_rfp_value eq $id"),
    @('hrs_clarifications', 'hrs_clarificationid', "_hrs_rfp_value eq $id"),
    @('hrs_gaps', 'hrs_gapid', "hrs_requirement/_hrs_rfp_value eq $id"),
    @('hrs_responses', 'hrs_responseid', "hrs_requirement/_hrs_rfp_value eq $id"),
    @('hrs_flags', 'hrs_flagid', "_hrs_rfp_value eq $id"),
    @('hrs_requirements', 'hrs_requirementid', "_hrs_rfp_value eq $id"),
    @('hrs_documentblocks', 'hrs_documentblockid', "hrs_rfpdocument/hrs_rfprevision/_hrs_rfp_value eq $id"),
    @('hrs_answers', 'hrs_answerid', "startswith(hrs_answercode,'PROP-$Key-') and hrs_approvalstatus eq 431200011")
)
foreach ($t in $tables) {
    $ids = @(Get-Ids $t[0] $t[1] $t[2])
    foreach ($rowId in $ids) { if ($PSCmdlet.ShouldProcess("$($t[0])($rowId)", 'delete')) { Invoke-Dv -Method DELETE -Path "$($t[0])($rowId)" | Out-Null } }
    Write-Host ("  {0,-22} {1} deleted" -f $t[0], $ids.Count)
}

if ($PSCmdlet.ShouldProcess("hrs_rfps($id)", "set stage $($FromStage - 1), Running")) {
    # Two writes: the pipeline triggers on the status change, so the stage must already be in place.
    $reset = @{ hrs_stage = $FromStage - 1; hrs_status = 431200170 }
    if ($FromStage -le 2) { $reset.hrs_currentrevision = 0 }
    Invoke-Dv -Method PATCH -Path "hrs_rfps($id)" -Body $reset | Out-Null
    Invoke-Dv -Method PATCH -Path "hrs_rfps($id)" -Body @{ hrs_status = 431200171 } | Out-Null
}
Write-Host "RFP $Key rewound: the pipeline resumes at stage $FromStage."
