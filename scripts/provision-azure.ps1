<#
.SYNOPSIS
  Creates the Azure resources for the rfp-tools service in dev or test (ADR-0031). Idempotent.

.DESCRIPTION
  Resource group, storage account, a Python 3.12 Flex Consumption Function App (Application
  Insights is created with it), and a Document Intelligence resource (S0, custom subdomain, local auth
  disabled) that the Function App calls with its system-assigned identity (ADR-0023). Names and region come from scripts/env.config. Uses the signed-in
  az account's active subscription; no keys or IDs are written anywhere (hard rule 4).

.PARAMETER AllowSignedInUser
  Also grants the signed-in az account the data-plane role on Document Intelligence, so Layout
  responses can be recorded as test fixtures from a developer machine (dev only).
.EXAMPLE
  ./scripts/provision-azure.ps1
  ./scripts/provision-azure.ps1 -AllowSignedInUser
  ./scripts/provision-azure.ps1 -Target test
#>
[CmdletBinding()]
param([ValidateSet('dev', 'test')][string]$Target = 'dev', [switch]$AllowSignedInUser)

. (Join-Path $PSScriptRoot 'lib\Dataverse.ps1')
if ($AllowSignedInUser -and $Target -ne 'dev') { throw '-AllowSignedInUser is for dev only' }
$res = Get-TargetResources -Target $Target
$loc = $res.Location
$rg = $res.ResourceGroup
$sa = $res.StorageAccount
$app = $res.FunctionApp
$di = $res.DocIntel

function Invoke-Az {
    $out = az @args 2>&1
    if ($LASTEXITCODE -ne 0) { throw "az $($args -join ' ') failed: $out" }
    return $out
}

Write-Host "Subscription: $(Invoke-Az account show --query name -o tsv)"

foreach ($p in 'Microsoft.Web', 'Microsoft.Insights', 'Microsoft.CognitiveServices') {
    if ((Invoke-Az provider show -n $p --query registrationState -o tsv) -ne 'Registered') {
        Write-Host "  registering provider $p (can take a few minutes)"
        Invoke-Az provider register -n $p --wait | Out-Null
    }
}

if ((Invoke-Az group exists -n $rg) -ne 'true') {
    Invoke-Az group create -n $rg -l $loc --tags project=rfp-factory env=$Target | Out-Null
    Write-Host "  + resource group $rg"
} else { Write-Host "  = resource group $rg" }

$saExists = az storage account show -n $sa -g $rg --query name -o tsv 2>$null
if (-not $saExists) {
    Invoke-Az storage account create -n $sa -g $rg -l $loc --sku Standard_LRS --kind StorageV2 `
        --min-tls-version TLS1_2 --allow-blob-public-access false --tags project=rfp-factory env=$Target | Out-Null
    Write-Host "  + storage account $sa"
} else { Write-Host "  = storage account $sa" }

$appExists = az functionapp show -n $app -g $rg --query name -o tsv 2>$null
if (-not $appExists) {
    Invoke-Az functionapp create -n $app -g $rg --storage-account $sa --flexconsumption-location $loc `
        --runtime python --runtime-version 3.12 --tags project=rfp-factory env=$Target | Out-Null
    Write-Host "  + function app $app (Flex Consumption, Python 3.12)"
} else { Write-Host "  = function app $app" }

# Document Intelligence: keyless only. A custom subdomain is required for Entra ID auth, and local (key)
# auth is disabled so no key can be used even if one is read from the portal.
$diExists = az cognitiveservices account show -n $di -g $rg --query name -o tsv 2>$null
if (-not $diExists) {
    Invoke-Az cognitiveservices account create -n $di -g $rg -l $loc --kind FormRecognizer --sku S0 `
        --custom-domain $di --yes --tags project=rfp-factory env=$Target | Out-Null
    Write-Host "  + document intelligence $di (S0)"
} else { Write-Host "  = document intelligence $di" }
$diId = Invoke-Az cognitiveservices account show -n $di -g $rg --query id -o tsv
if ((Invoke-Az resource show --ids $diId --query properties.disableLocalAuth -o tsv) -ne 'true') {
    Invoke-Az resource update --ids $diId --set properties.disableLocalAuth=true | Out-Null
    Write-Host "  ~ local auth disabled on $di"
}
$diEndpoint = Invoke-Az cognitiveservices account show -n $di -g $rg --query properties.endpoint -o tsv

# The Function App's system-assigned identity calls Document Intelligence (Cognitive Services User is the
# data-plane role; Contributor roles do not grant it). Role assignments take a few minutes to propagate.
$principal = Invoke-Az functionapp identity assign -n $app -g $rg --query principalId -o tsv
$role = 'Cognitive Services User'
# (Counted here, not with --query length(@): az.cmd passes the parentheses through cmd, which breaks them.)
# Filtered by principalId locally: --assignee looks the identity up in Entra ID, and a just-created identity isn't
# there yet, which fails a first run (seen 2026-11-09 in a fresh environment).
$has = @(Invoke-Az role assignment list --scope $diId --role $role -o json | ConvertFrom-Json | Where-Object principalId -eq $principal)
if ($has.Count -eq 0) {
    Invoke-Az role assignment create --assignee-object-id $principal --assignee-principal-type ServicePrincipal `
        --role $role --scope $diId | Out-Null
    Write-Host "  + role '$role' for $app on $di"
} else { Write-Host "  = role '$role' for $app on $di" }
if ($AllowSignedInUser) {
    $me = Invoke-Az ad signed-in-user show --query id -o tsv
    if (@(Invoke-Az role assignment list --assignee $me --scope $diId --role $role -o json | ConvertFrom-Json).Count -eq 0) {
        Invoke-Az role assignment create --assignee-object-id $me --assignee-principal-type User --role $role --scope $diId | Out-Null
        Write-Host "  + role '$role' for the signed-in user on $di"
    } else { Write-Host "  = role '$role' for the signed-in user on $di" }
}
Invoke-Az functionapp config appsettings set -n $app -g $rg --settings "DOCINTEL_ENDPOINT=$diEndpoint" | Out-Null
Write-Host "  = app setting DOCINTEL_ENDPOINT"

# `az functionapp show` returns null top-level fields for Flex Consumption apps; read the ARM resource instead.
$hostName = Invoke-Az resource show -g $rg -n $app --resource-type Microsoft.Web/sites --query properties.defaultHostName -o tsv
Write-Host "rfp-tools host: https://$hostName"
