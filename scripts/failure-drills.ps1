<#
.SYNOPSIS
  Dev only: Slice 7 failure drills (ADR-0026, ADR-0027). Each drill sets up a failure, waits, checks the outcome in
  Dataverse and restores what it changed. Results go to docs/RUNBOOK.md by hand.

.DESCRIPTION
  -Drill c   G6 breach: RFP B's page limit set to 1, rerun from stage 8. Expect: stage 8 run-log row Failed with the
             G6 message, the RFP Failed, and no "retrying once" (gates never retry). The page limit is restored.
  -Drill ef  Card waits with hrs_ReminderAfter = hrs_EscalateAfter = PT2M:
             e) RFP B reruns from stage 8 (approval card at stage 9), a test gap on RFP S and a test OCR flag on RFP D get their own
                cards; none is answered. Expect a reminder and an escalation in Teams, then "Approval overdue" and
                "Verification overdue" run-log rows and the gap Overdue.
             f) RFP B reruns from stage 8 again and its approval row is deleted while the card waits. Expect the card flow
                to end with the "withdrawn" notice.
             The timings are reset to the PT24H default and the test gap and flag are removed.
  -Drill g   Addendum during a run: a full scored RFP D run (eval/run.ps1 -Rfp D) with Addendum No. 1 uploaded while the
             base run is between stages 3 and 6. Expect the pipeline to log "An addendum arrived during the run" at
             stage 7, apply revision 1 at stage 3 and finish; the harness then skips the addendum phase (already
             applied) and scores the run.
  Drills a, b (fault injection, retry once) and h (hooks) run elsewhere: see docs/RUNBOOK.md.

.EXAMPLE
  ./scripts/failure-drills.ps1 -Drill c
#>
[CmdletBinding()]
param([Parameter(Mandatory)][ValidateSet('c', 'ef', 'g')][string]$Drill, [int]$WaitMinutes = 9)

. (Join-Path $PSScriptRoot 'lib\Dataverse.ps1')
$url = Resolve-TargetUrl -Target dev
Connect-Dataverse -Url $url
$cardFlows = 'RFP - Route Gap To SME', 'RFP - Verify OCR Requirement', 'RFP - Request Approval'

# No row yet is a normal answer: a drill can poll while a scored run has just reset the RFP. Returns $null then.
function Get-Rfp([string]$key) { (Invoke-Dv -Path "hrs_rfps?`$select=hrs_rfpid,hrs_stage,hrs_status,hrs_pagelimit&`$filter=hrs_rfpkey eq '$key'").value | Select-Object -First 1 }
function Get-RunLogs([string]$rfpId, [string]$since) {
    @((Invoke-Dv -Path "hrs_runlogs?`$select=hrs_name,hrs_stage,hrs_status,hrs_errordetail&`$filter=_hrs_rfp_value eq $rfpId and createdon ge $since&`$orderby=createdon").value)
}
function Wait-Until([scriptblock]$done, [int]$minutes) {
    $deadline = (Get-Date).AddMinutes($minutes)
    while ((Get-Date) -lt $deadline) { Start-Sleep -Seconds 30; Connect-Dataverse -Url $url; if (& $done) { return $true } }
    return $false
}
function Set-CardTiming([string]$duration) {
    # A value row overrides the PT24H default; removing it restores the default. Flows read environment variable
    # values when they are turned on, so the card flows are switched off and on again.
    foreach ($name in 'hrs_ReminderAfter', 'hrs_EscalateAfter') {
        $def = (Invoke-Dv -Path "environmentvariabledefinitions?`$select=environmentvariabledefinitionid&`$filter=schemaname eq '$name'").value[0]
        $val = (Invoke-Dv -Path "environmentvariablevalues?`$select=environmentvariablevalueid&`$filter=_environmentvariabledefinitionid_value eq $($def.environmentvariabledefinitionid)").value | Select-Object -First 1
        if ($duration) {
            if ($val) { Invoke-Dv -Method PATCH -Path "environmentvariablevalues($($val.environmentvariablevalueid))" -Body @{ value = $duration } | Out-Null }
            else { Invoke-Dv -Method POST -Path 'environmentvariablevalues' -Body @{ schemaname = "$($name)_value"; value = $duration; 'EnvironmentVariableDefinitionId@odata.bind' = "/environmentvariabledefinitions($($def.environmentvariabledefinitionid))" } | Out-Null }
        } elseif ($val) { Invoke-Dv -Method DELETE -Path "environmentvariablevalues($($val.environmentvariablevalueid))" | Out-Null }
    }
    & (Join-Path $PSScriptRoot 'apply-flows.ps1') -Only $cardFlows -Deactivate | Out-Null
    & (Join-Path $PSScriptRoot 'apply-flows.ps1') -Only $cardFlows | Out-Null
    Write-Host "Card timings: $(if ($duration) { $duration } else { 'default (PT24H)' })"
}
function Report([string]$name, [bool]$ok, [string]$detail) { Write-Host ("DRILL {0}: {1} - {2}" -f $name, $(if ($ok) { 'PASS' } else { 'FAIL' }), $detail) }

$since = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
switch ($Drill) {
    'c' {
        $b = Get-Rfp 'B'
        $limit = $b.hrs_pagelimit
        Invoke-Dv -Method PATCH -Path "hrs_rfps($($b.hrs_rfpid))" -Body @{ hrs_pagelimit = 1 } | Out-Null
        try {
            & (Join-Path $PSScriptRoot 'rewind-run.ps1') -Key B -FromStage 8
            $stopped = Wait-Until { (Get-Rfp 'B').hrs_status -eq 431200174 } $WaitMinutes
            Start-Sleep -Seconds 60  # a retry, if one were (wrongly) started, would show by now
            $logs = Get-RunLogs $b.hrs_rfpid $since
            $s8 = @($logs | Where-Object { $_.hrs_stage -eq 8 })
            $g6 = $s8 | Where-Object { $_.hrs_status -eq 431200182 -and $_.hrs_errordetail -match 'G6' }
            $retried = $logs | Where-Object { $_.hrs_errordetail -match 'retrying once' }
            Report 'c (G6 breach stops, no retry)' ($stopped -and $g6 -and -not $retried -and $s8.Count -eq 1) "RFP Failed: $stopped; stage 8 rows: $($s8.Count); G6 failure: $([bool]$g6); retry: $([bool]$retried). $($g6.hrs_errordetail)"
        } finally {
            Invoke-Dv -Method PATCH -Path "hrs_rfps($($b.hrs_rfpid))" -Body @{ hrs_pagelimit = $limit } | Out-Null
            Write-Host "RFP B page limit restored to $limit"
        }
    }
    'g' {
        $repo = Resolve-Path (Join-Path $PSScriptRoot '..')
        $log = Join-Path $env:TEMP "drill-g-$((Get-Date).ToString('yyyyMMddHHmmss')).log"
        $job = Start-Process pwsh -PassThru -WindowStyle Hidden -ArgumentList "-NoProfile -Command `"Set-Location '$repo'; ./eval/run.ps1 -Rfp D -Note 'Slice 7 drill g: Addendum No. 1 uploaded during the base run' *> '$log'`""
        Write-Host "RFP D run started (log $log); waiting for stages 3-6"
        $uploaded = Wait-Until {
            $d = Get-Rfp 'D'
            $d -and $d.hrs_status -eq 431200171 -and $d.hrs_stage -ge 3 -and $d.hrs_stage -le 5
        } 60
        if (-not $uploaded) { Report 'g (addendum during a run)' $false 'the base run never reached stages 3-6 while Running'; break }
        $file = Get-ChildItem (Join-Path $repo 'data\RFP_Synthetic_Dataset_Pilot/01_Inbound_RFPs') -Filter 'RFP-D_*Addendum*' | Select-Object -First 1
        $cfg = Get-EnvConfig
        $h = @{ Authorization = "Bearer $(az account get-access-token --resource https://graph.microsoft.com --query accessToken -o tsv)" }
        $gid = (Invoke-RestMethod -Headers $h "https://graph.microsoft.com/v1.0/groups?`$filter=mailNickname eq '$($cfg.SHAREPOINT_SITE_ALIAS)'&`$select=id").value[0].id
        Invoke-RestMethod -Method PUT -Headers $h -ContentType 'application/octet-stream' -InFile $file.FullName `
            "https://graph.microsoft.com/v1.0/groups/$gid/drive/root:/Inbound/$([uri]::EscapeDataString($file.Name)):/content" | Out-Null
        Write-Host "Uploaded $($file.Name) at stage $((Get-Rfp 'D').hrs_stage)"
        $job.WaitForExit()
        $d = Get-Rfp 'D'
        $restart = Get-RunLogs $d.hrs_rfpid $since | Where-Object { $_.hrs_errordetail -match 'addendum arrived during the run' }
        $rev = (Invoke-Dv -Path "hrs_rfps($($d.hrs_rfpid))?`$select=hrs_currentrevision").hrs_currentrevision
        Report 'g (addendum during a run)' ([bool]$restart -and $rev -ge 1) "restart logged: $([bool]$restart); current revision $rev; harness exit $($job.ExitCode) (0 = PASS); log $log"
    }
    'ef' {
        Set-CardTiming 'PT2M'
        try {
            $b = Get-Rfp 'B'; $s = Get-Rfp 'S'; $d = Get-Rfp 'D'
            # e) approval card (rerun from stage 8; stage 9 needs the package stage 8 built in the same run), a gap card and an OCR verification card, none answered.
            & (Join-Path $PSScriptRoot 'rewind-run.ps1') -Key B -FromStage 8
            $sReq = (Invoke-Dv -Path "hrs_requirements?`$select=hrs_requirementid,hrs_reqid&`$filter=_hrs_rfp_value eq $($s.hrs_rfpid)&`$top=1").value[0]
            $sme = (Invoke-Dv -Path "hrs_smes?`$select=hrs_smeid&`$top=1").value[0]
            $gap = Invoke-Dv -Method POST -Path 'hrs_gaps' -Headers @{ Prefer = 'return=representation' } -Body @{
                hrs_name = "DRILL gap $($sReq.hrs_reqid)"; 'hrs_requirement@odata.bind' = "/hrs_requirements($($sReq.hrs_requirementid))"
                'hrs_routedtosme@odata.bind' = "/hrs_smes($($sme.hrs_smeid))"; hrs_routedon = $since; hrs_dueby = $since; hrs_smestatus = 431200190 }
            $dReq = (Invoke-Dv -Path "hrs_requirements?`$select=hrs_requirementid,hrs_reqid&`$filter=_hrs_rfp_value eq $($d.hrs_rfpid) and hrs_reqid eq 'THPA-005'").value[0]
            $flag = Invoke-Dv -Method POST -Path 'hrs_flags' -Headers @{ Prefer = 'return=representation' } -Body @{
                hrs_name = "DRILL OCR-LOW-CONFIDENCE THPA-005"; hrs_flagtype = (Invoke-Dv -Path "hrs_flags?`$select=hrs_flagtype&`$filter=startswith(hrs_name,'OCR-LOW-CONFIDENCE')&`$top=1").value[0].hrs_flagtype
                hrs_severity = 431200051; hrs_detail = 'Failure drill e (ADR-0027); not a real OCR flag.'
                'hrs_rfp@odata.bind' = "/hrs_rfps($($d.hrs_rfpid))"; 'hrs_requirement@odata.bind' = "/hrs_requirements($($dReq.hrs_requirementid))" }
            Write-Host "Cards posted at $since; waiting for reminders, escalations and timeouts (3 x 2 min)"
            $ok = Wait-Until {
                $bl = Get-RunLogs $b.hrs_rfpid $since; $dl = Get-RunLogs $d.hrs_rfpid $since
                $g = Invoke-Dv -Path "hrs_gaps($($gap.hrs_gapid))?`$select=hrs_smestatus"
                ($bl | Where-Object { $_.hrs_name -eq 'Approval overdue' }) -and ($dl | Where-Object { $_.hrs_name -like 'Verification overdue*' }) -and $g.hrs_smestatus -eq 431200193
            } $WaitMinutes
            $g = Invoke-Dv -Path "hrs_gaps($($gap.hrs_gapid))?`$select=hrs_smestatus"
            Report 'e (reminder, escalation, overdue for all three card flows)' $ok "gap status $($g.hrs_smestatus) (Overdue expected); see the Teams chat for reminder and escalation messages"

            # f) approval row removed while its card waits: the flow ends with the withdrawn notice.
            $since2 = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
            & (Join-Path $PSScriptRoot 'rewind-run.ps1') -Key B -FromStage 8
            $approval = $null
            Wait-Until { $script:approval = (Invoke-Dv -Path "hrs_approvals?`$select=hrs_approvalid&`$filter=_hrs_rfp_value eq $($b.hrs_rfpid) and createdon ge $since2").value | Select-Object -First 1; [bool]$script:approval } 10 | Out-Null
            Start-Sleep -Seconds 30
            Invoke-Dv -Method DELETE -Path "hrs_approvals($($script:approval.hrs_approvalid))" | Out-Null
            Write-Host "Approval $($script:approval.hrs_approvalid) deleted while its card waits"
            $envId = Get-PowerPlatformEnvId -Url $url
            $flowId = (Invoke-Dv -Path "workflows?`$select=workflowid&`$filter=name eq 'RFP - Request Approval' and category eq 5").value[0].workflowid
            $h = @{ Authorization = "Bearer $(az account get-access-token --resource https://service.flow.microsoft.com/ --query accessToken -o tsv)" }
            $base = "https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple/environments/$envId/flows/$flowId"
            $notice = Wait-Until {
                $run = (Invoke-FlowApi -Headers $h "$base/runs?api-version=2016-11-01&`$top=1").value[0]
                try { (Invoke-FlowApi -Headers $h "$base/runs/$($run.name)/actions/Tell_Withdrawn?api-version=2016-11-01").properties.status -eq 'Succeeded' } catch { $false }
            } $WaitMinutes
            Report 'f (superseded card answered with the withdrawn notice)' $notice "approval card flow's Tell_Withdrawn action"
            Invoke-Dv -Method DELETE -Path "hrs_gaps($($gap.hrs_gapid))" | Out-Null
            Invoke-Dv -Method DELETE -Path "hrs_flags($($flag.hrs_flagid))" | Out-Null
            Write-Host 'Test gap and flag removed'
            # The test gap's flow resumes RFP S when no gap is still Routed; S had finished, so put it back.
            Invoke-Dv -Method PATCH -Path "hrs_rfps($($s.hrs_rfpid))" -Body @{ hrs_status = 431200172 } | Out-Null
        } finally {
            Set-CardTiming $null
        }
    }
}
