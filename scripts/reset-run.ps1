<#
.SYNOPSIS
  Dev or test only: removes every runtime row and output file for one RFP, so the run can start again from the
  inbound upload. Reference data is never touched.

.DESCRIPTION
  Deletes, for the RFP with the given key: approvals, clarifications, gaps, responses, flags, requirements,
  document blocks, documents, revisions, run logs and the RFP row; Draft Library entries proposed from its
  gaps (PROP-<key>-*); and its files in Inbound, Output/Draft and Output/Filed. Refuses to run against anything but dev or test, and uses only
  the target's own SharePoint site (ADR-0031).

.EXAMPLE
  ./scripts/reset-run.ps1 -Key A
  ./scripts/reset-run.ps1 -Key A -Target test
#>
[CmdletBinding(SupportsShouldProcess)]
param([Parameter(Mandatory)][string]$Key, [ValidateSet('dev', 'test')][string]$Target = 'dev')

. (Join-Path $PSScriptRoot 'lib\Dataverse.ps1')
Connect-Dataverse -Url (Resolve-TargetUrl -Target $Target)

function Remove-Rows([string]$set, [string]$idColumn, [string]$filter) {
    $rows = @((Invoke-Dv -Path "$set`?`$select=$idColumn&`$filter=$([uri]::EscapeDataString($filter))").value)
    foreach ($r in $rows) {
        if ($PSCmdlet.ShouldProcess("$set($($r.$idColumn))", 'delete')) {
            # A row can already be gone, removed along with one deleted just before it.
            try { Invoke-Dv -Method DELETE -Path "$set($($r.$idColumn))" | Out-Null } catch { if ("$_" -notmatch 'failed \(404\)') { throw } }
        }
    }
    Write-Host ("  {0,-22} {1} deleted" -f $set, $rows.Count)
}

$rfp = (Invoke-Dv -Path "hrs_rfps?`$select=hrs_rfpid&`$filter=hrs_rfpkey eq '$Key'").value | Select-Object -First 1
if ($rfp) {
    $id = $rfp.hrs_rfpid
    Remove-Rows 'hrs_approvals' 'hrs_approvalid' "_hrs_rfp_value eq $id"
    Remove-Rows 'hrs_clarifications' 'hrs_clarificationid' "_hrs_rfp_value eq $id"
    Remove-Rows 'hrs_gaps' 'hrs_gapid' "hrs_requirement/_hrs_rfp_value eq $id"
    Remove-Rows 'hrs_responses' 'hrs_responseid' "hrs_requirement/_hrs_rfp_value eq $id"
    Remove-Rows 'hrs_flags' 'hrs_flagid' "_hrs_rfp_value eq $id"
    Remove-Rows 'hrs_requirements' 'hrs_requirementid' "_hrs_rfp_value eq $id"
    foreach ($rev in (Invoke-Dv -Path "hrs_rfprevisions?`$select=hrs_rfprevisionid&`$filter=_hrs_rfp_value eq $id").value) {
        foreach ($doc in (Invoke-Dv -Path "hrs_rfpdocuments?`$select=hrs_rfpdocumentid&`$filter=_hrs_rfprevision_value eq $($rev.hrs_rfprevisionid)").value) {
            Remove-Rows 'hrs_documentblocks' 'hrs_documentblockid' "_hrs_rfpdocument_value eq $($doc.hrs_rfpdocumentid)"
        }
        Remove-Rows 'hrs_rfpdocuments' 'hrs_rfpdocumentid' "_hrs_rfprevision_value eq $($rev.hrs_rfprevisionid)"
    }
    Remove-Rows 'hrs_rfprevisions' 'hrs_rfprevisionid' "_hrs_rfp_value eq $id"
    Remove-Rows 'hrs_runlogs' 'hrs_runlogid' "_hrs_rfp_value eq $id"
    Invoke-Dv -Method DELETE -Path "hrs_rfps($id)" | Out-Null
    Write-Host "  hrs_rfps               1 deleted"
} else { Write-Host "  no hrs_rfp with key $Key" }
Remove-Rows 'hrs_answers' 'hrs_answerid' "startswith(hrs_answercode,'PROP-$Key-') and hrs_approvalstatus eq 431200011"

# Files: this RFP's Inbound uploads (base document and addenda, RFP-<key>_*) and its Output/Draft and Output/Filed
# package files (<key>-*). Other RFPs' files stay: a later rewind of another RFP reads its source document from Inbound.
$res = Get-TargetResources -Target $Target
$gtok = az account get-access-token --resource https://graph.microsoft.com --query accessToken -o tsv
$h = @{ Authorization = "Bearer $gtok" }
$gid = (Invoke-RestMethod -Headers $h "https://graph.microsoft.com/v1.0/groups?`$filter=mailNickname eq '$($res.SiteAlias)'&`$select=id").value[0].id
foreach ($folder in 'Inbound', 'Output/Draft', 'Output/Filed') {
    $items = (Invoke-RestMethod -Headers $h "https://graph.microsoft.com/v1.0/groups/$gid/drive/root:/$($folder):/children?`$select=id,name").value
    $mine = @($items | Where-Object { $_.name -like $(if ($folder -eq 'Inbound') { "RFP-$Key`_*" } else { "$Key-*" }) })
    foreach ($i in $mine) { if ($PSCmdlet.ShouldProcess("$folder/$($i.name)", 'delete')) { Invoke-RestMethod -Method DELETE -Headers $h "https://graph.microsoft.com/v1.0/groups/$gid/drive/items/$($i.id)" | Out-Null } }
    Write-Host ("  {0,-22} {1} file(s) deleted" -f $folder, $mine.Count)
}
