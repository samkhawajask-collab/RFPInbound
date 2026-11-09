<#
.SYNOPSIS
  Sets environment variable values in dev/test from scripts/settings/<target>.local.json.

.DESCRIPTION
  The values file uses the deployment-settings format ("EnvironmentVariables": [{SchemaName, Value}])
  and is git-ignored, because the values are environment-specific (URLs, the pilot recipient's UPN).
  Value rows are written WITHOUT the solution header, so they stay out of the RFPFactory solution
  and never appear in an export (hard rule 4).

.EXAMPLE
  ./scripts/set-env-values.ps1 -Target dev
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('dev', 'test')][string]$Target
)

. (Join-Path $PSScriptRoot 'lib\Dataverse.ps1')
$file = Join-Path $script:RepoRoot "scripts\settings\$Target.local.json"
if (-not (Test-Path $file)) { throw "$file not found. Copy deployment-settings.template.json and fill in the values." }
$settings = Get-Content $file -Raw | ConvertFrom-Json

Connect-Dataverse -Url (Resolve-TargetUrl -Target $Target)
foreach ($ev in $settings.EnvironmentVariables) {
    if ([string]::IsNullOrWhiteSpace($ev.Value)) { Write-Host "  - $($ev.SchemaName): no value in $Target.local.json, skipped"; continue }
    $def = (Invoke-Dv -Path "environmentvariabledefinitions?`$select=environmentvariabledefinitionid&`$filter=schemaname eq '$($ev.SchemaName)'").value | Select-Object -First 1
    if (-not $def) { Write-Warning "  ! $($ev.SchemaName) is not defined in $Target; run apply-schema / import first"; continue }
    $existing = (Invoke-Dv -Path "environmentvariablevalues?`$select=environmentvariablevalueid,value&`$filter=_environmentvariabledefinitionid_value eq $($def.environmentvariabledefinitionid)").value | Select-Object -First 1
    if ($existing) {
        if ($existing.value -ne $ev.Value) {
            Invoke-Dv -Method PATCH -Path "environmentvariablevalues($($existing.environmentvariablevalueid))" -Body @{ value = $ev.Value } -Headers @{ 'If-Match' = '*' } | Out-Null
            Write-Host "  ~ $($ev.SchemaName) updated"
        } else { Write-Host "  = $($ev.SchemaName)" }
    } else {
        Invoke-Dv -Method POST -Path 'environmentvariablevalues' -Body @{
            schemaname = "$($ev.SchemaName)_value"
            value = $ev.Value
            'EnvironmentVariableDefinitionId@odata.bind' = "/environmentvariabledefinitions($($def.environmentvariabledefinitionid))"
        } | Out-Null
        Write-Host "  + $($ev.SchemaName) set"
    }
}

# Bind connection references to connections ({LogicalName, ConnectionId}). Bindings are environment state, not source.
foreach ($cr in @($settings.ConnectionReferences)) {
    if (-not $cr -or [string]::IsNullOrWhiteSpace($cr.ConnectionId)) { continue }
    $ref = (Invoke-Dv -Path "connectionreferences?`$select=connectionreferenceid,connectionid&`$filter=connectionreferencelogicalname eq '$($cr.LogicalName)'").value | Select-Object -First 1
    if (-not $ref) { Write-Warning "  ! connection reference $($cr.LogicalName) not found in $Target"; continue }
    if ($ref.connectionid -eq $cr.ConnectionId) { Write-Host "  = $($cr.LogicalName) bound"; continue }
    Invoke-Dv -Method PATCH -Path "connectionreferences($($ref.connectionreferenceid))" -Body @{ connectionid = $cr.ConnectionId } -Headers @{ 'If-Match' = '*' } | Out-Null
    Write-Host "  ~ $($cr.LogicalName) bound"
}
