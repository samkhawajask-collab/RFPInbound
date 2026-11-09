<#
.SYNOPSIS
  Runs the rfp-tools tests, then deploys tools/service + tools/rfp_tools to the target's Function App.

.DESCRIPTION
  Builds a zip (function_app.py, host.json, requirements.txt, rfp_tools/) in solution/build and
  deploys it with a remote build. Refuses to deploy if any test fails, including the gold-data
  isolation test. No keys are printed or stored; the function key stays in Azure and in the
  connector connection.

.EXAMPLE
  ./scripts/deploy-tools.ps1
  ./scripts/deploy-tools.ps1 -Target test
#>
[CmdletBinding()]
param([ValidateSet('dev', 'test')][string]$Target = 'dev', [switch]$SkipTests)

. (Join-Path $PSScriptRoot 'lib\Dataverse.ps1')
$res = Get-TargetResources -Target $Target
$tools = Join-Path $script:RepoRoot 'tools'
$python = Join-Path $tools '.venv\Scripts\python.exe'

if (-not $SkipTests) {
    Push-Location $tools
    try { & $python -m pytest -q; if ($LASTEXITCODE -ne 0) { throw 'Tests failed; not deploying.' } } finally { Pop-Location }
}

$stage = Join-Path $script:RepoRoot 'solution\build\rfp-tools'
Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $stage | Out-Null
Copy-Item (Join-Path $tools 'service\function_app.py'), (Join-Path $tools 'service\host.json'), (Join-Path $tools 'requirements.txt') $stage
Copy-Item -Recurse (Join-Path $tools 'rfp_tools') (Join-Path $stage 'rfp_tools')
# Choice values come from the schema spec (rfp_tools/schema.py); package the same file.
Copy-Item (Join-Path $script:RepoRoot 'solution\spec\tables.json') (Join-Path $stage 'rfp_tools\tables.json')
Get-ChildItem $stage -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
$zip = Join-Path $script:RepoRoot 'solution\build\rfp-tools.zip'
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip

$out = az functionapp deployment source config-zip -g $res.ResourceGroup -n $res.FunctionApp --src $zip --build-remote true 2>&1
if ($LASTEXITCODE -ne 0) { throw "Deployment failed: $out" }

$hostName = az resource show -g $res.ResourceGroup -n $res.FunctionApp --resource-type Microsoft.Web/sites --query properties.defaultHostName -o tsv
$key = az functionapp function keys list -g $res.ResourceGroup -n $res.FunctionApp --function-name health --query default -o tsv 2>$null
if (-not $key) { $key = az functionapp keys list -g $res.ResourceGroup -n $res.FunctionApp --query functionKeys.default -o tsv }
for ($i = 0; $i -lt 12; $i++) {
    try {
        $h = Invoke-RestMethod "https://$hostName/api/health?code=$key"
        Write-Host "rfp-tools healthy at https://$hostName - $($h.operations.Count) operations"
        return
    } catch { Start-Sleep -Seconds 10 }
}
throw "Deployed, but https://$hostName/api/health did not respond"
