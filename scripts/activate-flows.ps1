<#
.SYNOPSIS
  Turns on every "RFP - " flow in the target and arms its runtime, without changing any definition.

.DESCRIPTION
  For test, where flows arrive in a managed solution and apply-flows.ps1 must not edit them (ADR-0031). Run it after
  the connection references are bound. A flow that depends on one not yet on can fail, so failures are retried. Exits
  non-zero unless all flows are on and started.

.EXAMPLE
  ./scripts/activate-flows.ps1 -Target test
#>
[CmdletBinding()]
param([ValidateSet('dev', 'test')][string]$Target = 'test', [int]$Passes = 3)

. (Join-Path $PSScriptRoot 'lib\Dataverse.ps1')
$url = Resolve-TargetUrl -Target $Target
Connect-Dataverse -Url $url
$envId = Get-PowerPlatformEnvId -Url $url
$paBase = "https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple/environments/$envId/flows"
function Invoke-PA([string]$Method, [string]$Path) {
    $tok = az account get-access-token --resource https://service.flow.microsoft.com/ --query accessToken -o tsv
    Invoke-RestMethod -Method $Method -Headers @{ Authorization = "Bearer $tok" } "$paBase/$Path`?api-version=2016-11-01"
}

$flows = @((Invoke-Dv -Path "workflows?`$select=workflowid,name&`$filter=startswith(name,'RFP - ') and category eq 5&`$orderby=name").value)
$pending = $flows
for ($pass = 1; $pass -le $Passes -and $pending.Count; $pass++) {
    $failed = @()
    foreach ($f in $pending) {
        try {
            $on = (Invoke-Dv -Path "workflows($($f.workflowid))?`$select=statecode").statecode -eq 1
            if ($on -and (Invoke-PA GET $f.workflowid).properties.state -eq 'Started') { Write-Host "  = $($f.name)"; continue }
            if (-not $on) {
                Invoke-Dv -Method PATCH -Path "workflows($($f.workflowid))" -Body @{ statecode = 1; statuscode = 2 } -Headers @{ 'If-Match' = '*' } | Out-Null
            }
            try { Invoke-PA POST "$($f.workflowid)/stop" | Out-Null } catch {}
            Invoke-PA POST "$($f.workflowid)/start" | Out-Null
            Write-Host "  on: $($f.name) (runtime $((Invoke-PA GET $f.workflowid).properties.state))"
        } catch {
            $msg = ($_.ErrorDetails.Message, $_.ToString() | Where-Object { $_ } | Select-Object -First 1)
            Write-Host "  ! pass $pass $($f.name): $($msg.Substring(0, [Math]::Min(600, $msg.Length)))"
            $failed += $f
        }
    }
    # A flow reported as started has been seen off a minute later (2026-11-01), so only a later read counts.
    Start-Sleep -Seconds 90
    Connect-Dataverse -Url $url
    $off = @($flows | Where-Object { (Invoke-Dv -Path "workflows($($_.workflowid))?`$select=statecode").statecode -ne 1 -and $_.workflowid -notin $failed.workflowid })
    $pending = @($failed) + $off
}
Write-Host "$($flows.Count - $pending.Count) of $($flows.Count) flows on"
if ($pending.Count) { exit 1 }
