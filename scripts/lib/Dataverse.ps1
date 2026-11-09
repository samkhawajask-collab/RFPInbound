# Shared helpers for scripts that talk to Dataverse. Dot-source this file.
# Auth comes from the signed-in Azure CLI account; no credentials are stored (hard rule 4).

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path

function Get-EnvConfig {
    $path = Join-Path $script:RepoRoot 'scripts\env.config'
    $cfg = @{}
    foreach ($line in Get-Content $path) {
        if ($line -match '^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$') { $cfg[$Matches[1]] = $Matches[2] }
    }
    return $cfg
}

# Resolves -Target dev|test to a URL from env.config. Refuses anything not listed (hard rule 3).
# The Azure and SharePoint names for a target (ADR-0031). A missing name throws, so a test command can never fall
# back to dev's resources.
function Get-TargetResources {
    param([Parameter(Mandatory)][ValidateSet('dev', 'test')][string]$Target)
    $cfg = Get-EnvConfig
    $prefix = if ($Target -eq 'test') { 'TEST_' } else { '' }
    $map = @{ ResourceGroup = 'AZURE_RESOURCE_GROUP'; StorageAccount = 'AZURE_STORAGE_ACCOUNT'; FunctionApp = 'AZURE_FUNCTION_APP'
        DocIntel = 'AZURE_DOCINTEL'; SiteAlias = 'SHAREPOINT_SITE_ALIAS'; SiteName = 'SHAREPOINT_SITE_NAME' }
    $r = @{ Location = $cfg['AZURE_LOCATION'] }
    foreach ($k in $map.Keys) { $r[$k] = $cfg["$prefix$($map[$k])"] }
    foreach ($k in @($r.Keys)) { if (-not $r[$k]) { throw "$k for $Target is missing from scripts/env.config" } }
    return $r
}

function Resolve-TargetUrl {
    param([Parameter(Mandatory)][ValidateSet('dev', 'test')][string]$Target)
    $cfg = Get-EnvConfig
    $url = $cfg["$($Target.ToUpper())_URL"]
    if ([string]::IsNullOrWhiteSpace($url)) { throw "No $($Target.ToUpper())_URL in scripts/env.config; refusing to continue." }
    return $url.TrimEnd('/')
}

function Connect-Dataverse {
    param([Parameter(Mandatory)][string]$Url)
    $token = az account get-access-token --resource $Url --query accessToken -o tsv
    if (-not $token) { throw "Could not get a token for $Url. Run 'az login' with the project account." }
    $script:DvUrl = $Url
    $script:DvHeaders = @{
        Authorization      = "Bearer $token"
        Accept             = 'application/json'
        'OData-MaxVersion' = '4.0'
        'OData-Version'    = '4.0'
    }
}

# Invokes the Web API. Returns $null on 404 when -AllowNotFound is set. Retries throttling (429) and transient 5xx.
function Invoke-Dv {
    param(
        [ValidateSet('GET', 'POST', 'PATCH', 'PUT', 'DELETE')][string]$Method = 'GET',
        [Parameter(Mandatory)][string]$Path,
        $Body,
        [hashtable]$Headers = @{},
        [switch]$AllowNotFound
    )
    $uri = "$script:DvUrl/api/data/v9.2/$Path"
    $h = $script:DvHeaders.Clone()
    foreach ($k in $Headers.Keys) { $h[$k] = $Headers[$k] }
    $params = @{ Method = $Method; Uri = $uri; Headers = $h }
    if ($null -ne $Body) {
        $params.Body = [System.Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Depth 30 -Compress))
        $params.ContentType = 'application/json; charset=utf-8'
    }
    for ($attempt = 1; ; $attempt++) {
        try {
            return Invoke-RestMethod @params
        } catch {
            # Strict mode: not every exception has a Response, and not every error has ErrorDetails.
            $response = $_.Exception.PSObject.Properties['Response']?.Value
            $status = if ($response) { [int]$response.StatusCode } else { 0 }
            if ($AllowNotFound -and $status -eq 404) { return $null }
            if (($status -eq 429 -or $status -ge 500 -or $status -eq 0) -and $attempt -lt 6) {
                Start-Sleep -Seconds ([math]::Min(60, 5 * $attempt))
                continue
            }
            $detail = if ($_.ErrorDetails) { $_.ErrorDetails.Message } else { $_.Exception.Message }
            throw "Dataverse $Method $Path failed ($status): $detail"
        }
    }
}

function New-DvLabel {
    param([Parameter(Mandatory)][string]$Text)
    @{
        '@odata.type'   = 'Microsoft.Dynamics.CRM.Label'
        LocalizedLabels = @(@{ '@odata.type' = 'Microsoft.Dynamics.CRM.LocalizedLabel'; Label = $Text; LanguageCode = 1033 })
    }
}

# Power Platform environment ID for an environment URL, from pac (no IDs are stored in the repo).
function Get-PowerPlatformEnvId {
    param([Parameter(Mandatory)][string]$Url)
    $id = (pac env list 2>$null | Select-String ([regex]::Escape($Url.TrimEnd('/'))) |
        ForEach-Object { ($_ -split '\s+') | Where-Object { $_ -match '^[0-9a-f-]{36}$' } } | Select-Object -First 1)
    if (-not $id) { throw "Could not resolve the environment ID for $Url from pac env list" }
    return $id
}

# Invoke-RestMethod with retries for transient Power Automate API failures (ServerTimeout, 429, 5xx).
function Invoke-FlowApi {
    param([Parameter(Mandatory)][string]$Uri, [hashtable]$Headers, [string]$Method = 'GET', [int]$Attempts = 4)
    for ($i = 1; ; $i++) {
        try { return Invoke-RestMethod -Method $Method -Headers $Headers $Uri }
        catch {
            $status = [int]$_.Exception.Response.StatusCode
            $transient = $status -eq 429 -or $status -ge 500 -or "$_" -match 'ServerTimeout'
            if (-not $transient -or $i -ge $Attempts) { throw }
            Start-Sleep -Seconds (15 * $i)
        }
    }
}

# Cancels running flow runs of one flow whose trigger record is in $RecordIds (or every running run when
# $RecordIds is $null). $RecordColumn names the trigger body column holding the record ID, e.g. hrs_gapid.
function Stop-FlowRuns {
    param([Parameter(Mandatory)][string]$EnvId, [Parameter(Mandatory)][string]$FlowName, [string]$RecordColumn, $RecordIds)
    $flowId = (Invoke-Dv -Path "workflows?`$select=workflowid&`$filter=name eq '$FlowName' and category eq 5").value[0].workflowid
    $tok = az account get-access-token --resource https://service.flow.microsoft.com/ --query accessToken -o tsv
    $h = @{ Authorization = "Bearer $tok" }
    $base = "https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple/environments/$EnvId/flows/$flowId"
    $runs = @((Invoke-FlowApi -Headers $h "$base/runs?api-version=2016-11-01&`$filter=status eq 'Running'").value)
    $stopped = 0
    foreach ($run in $runs) {
        if ($null -ne $RecordIds) {
            # The run list already carries the trigger's outputs link (there is no per-run /triggers resource).
            $link = $run.properties.trigger.outputsLink.uri
            $record = if ($link) { (Invoke-FlowApi $link).body.$RecordColumn } else { $null }
            if ($record -notin $RecordIds) { continue }
        }
        # A run can finish between the list and the cancel; that is the outcome we wanted.
        try { Invoke-FlowApi -Method POST -Headers $h "$base/runs/$($run.name)/cancel?api-version=2016-11-01" | Out-Null }
        catch { if ("$_" -match 'WorkflowRunCanNotBeCancelled') { continue } else { throw } }
        $stopped++
    }
    return $stopped
}
