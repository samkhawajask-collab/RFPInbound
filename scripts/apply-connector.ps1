<#
.SYNOPSIS
  Creates or updates the rfp-tools custom connector in dev from solution/spec/connector. Idempotent.

.DESCRIPTION
  Uses `pac connector create` / `pac connector update`, which register both the Dataverse connector row
  and the Power Apps API, so the solution can export the connector and its connection reference.
  (A connector written directly as a Dataverse row works at runtime but blocks solution export.)
  pac names the connector with the default publisher prefix: new_rfp-20factory-20tools. That name is
  a known deviation from the hrs_ convention, recorded in ARCHITECTURE.md section 12.2.

  The Host is @environmentVariables("hrs_RfpToolsBaseUrl"), so no environment URL is in source
  (hard rule 4). -CreateConnection also creates the connection, reading the function key from Azure
  at run time; the key is never written to disk or printed.

  In test the connector is managed and arrives with the solution, so -Target test only creates the connection, with
  the key from test's own Function App (ADR-0031).

.EXAMPLE
  ./scripts/apply-connector.ps1 -CreateConnection
  ./scripts/apply-connector.ps1 -Target test -CreateConnection
#>
[CmdletBinding()]
param([ValidateSet('dev', 'test')][string]$Target = 'dev', [switch]$CreateConnection)

. (Join-Path $PSScriptRoot 'lib\Dataverse.ps1')
$res = Get-TargetResources -Target $Target
$url = Resolve-TargetUrl -Target $Target
$specDir = Join-Path $script:RepoRoot 'solution\spec\connector'
$definition = Join-Path $specDir 'rfp-tools.openapi.json'
$properties = Join-Path $specDir 'rfp-tools.apiProperties.json'
$name = 'new_rfp-20factory-20tools'

Connect-Dataverse -Url $url
$existing = (Invoke-Dv -Path "connectors?`$select=connectorid&`$filter=name eq '$name'").value | Select-Object -First 1
if ($Target -eq 'test') {
    if (-not $existing) { throw "Connector $name not found in test; import the solution first" }
} elseif ($existing) {
    $out = pac connector update --connector-id $existing.connectorid --api-definition-file $definition --api-properties-file $properties --solution-unique-name RFPFactory --environment $url 2>&1
    if ($LASTEXITCODE -ne 0 -or ($out -match '^Error:')) { throw "pac connector update failed: $out" }
    Write-Host "  ~ connector $name updated"
} else {
    $out = pac connector create --api-definition-file $definition --api-properties-file $properties --solution-unique-name RFPFactory --environment $url 2>&1
    if ($LASTEXITCODE -ne 0 -or ($out -match '^Error:')) { throw "pac connector create failed: $out" }
    Write-Host "  + connector $name"
}
$connector = (Invoke-Dv -Path "connectors?`$select=connectorid,connectorinternalid&`$filter=name eq '$name'").value | Select-Object -First 1
Write-Host "  internal id: $($connector.connectorinternalid)"

if (-not $CreateConnection) { return }

$envId = (pac env list 2>$null | Select-String ([regex]::Escape($url)) | ForEach-Object { ($_ -split '\s+') | Where-Object { $_ -match '^[0-9a-f-]{36}$' } } | Select-Object -First 1)
if (-not $envId) { throw 'Could not resolve the $Target environment ID from pac env list' }
$key = az functionapp keys list -g $res.ResourceGroup -n $res.FunctionApp --query functionKeys.default -o tsv
$tok = az account get-access-token --resource https://service.powerapps.com/ --query accessToken -o tsv
$connName = [guid]::NewGuid().ToString('N')
$uri = "https://api.powerapps.com/providers/Microsoft.PowerApps/apis/$($connector.connectorinternalid)/connections/$connName`?api-version=2016-11-01&`$filter=environment eq '$envId'"
$payload = @{ properties = @{ environment = @{ id = "/providers/Microsoft.PowerApps/environments/$envId"; name = $envId }; displayName = "RFP Factory Tools ($Target)"; connectionParameters = @{ api_key = $key } } } | ConvertTo-Json -Depth 6
$conn = Invoke-RestMethod -Method PUT -Uri $uri -Headers @{ Authorization = "Bearer $tok" } -ContentType 'application/json' -Body $payload
$status = ($conn.properties.statuses | ForEach-Object status) -join ','
Write-Host "  + connection $connName ($status) - record it in scripts/settings/$Target.local.json as hrs_rfptoolsconnection"
$connName
