<#
.SYNOPSIS
  Imports the packed solution into dev or test - nothing else (hard rule 3).

.DESCRIPTION
  The target URL comes from scripts/env.config. The signed-in pac profile must be able to reach it;
  the script prints the identity it is using before importing. Dev takes the unmanaged package,
  test takes the managed one (ARCHITECTURE.md section 12.1). Deployment settings, when present,
  are read from scripts/settings/<target>.local.json (git-ignored).

.EXAMPLE
  ./scripts/import.ps1 -Target dev
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('dev', 'test')][string]$Target
)
. (Join-Path $PSScriptRoot 'lib\Dataverse.ps1')

$url = Resolve-TargetUrl -Target $Target
$zip = Join-Path $script:RepoRoot $(if ($Target -eq 'test') { 'solution\build\RFPFactory_managed.zip' } else { 'solution\build\RFPFactory.zip' })
if (-not (Test-Path $zip)) { throw "$zip not found; run scripts/pack.ps1$(if ($Target -eq 'test') { ' -PackageType Managed' }) first" }

pac org who --environment $url
if ($LASTEXITCODE -ne 0) { throw "pac cannot reach $url with the active profile" }

$pacArgs = @('solution', 'import', '--path', $zip, '--environment', $url, '--publish-changes', '--async')
$settings = Join-Path $script:RepoRoot "scripts\settings\$Target.local.json"
if (Test-Path $settings) {
    # pac rejects an empty environment variable value ("can't be an empty string"), so variables not set
    # for this target yet (for example hrs_DocIntelEndpoint before Slice 4) are left out of the import.
    $cfg = Get-Content $settings -Raw | ConvertFrom-Json
    $empty = @($cfg.EnvironmentVariables | Where-Object { -not $_.Value } | ForEach-Object { $_.SchemaName })
    if ($empty) {
        Write-Warning "No value for $($empty -join ', '); left out of the import"
        $cfg.EnvironmentVariables = @($cfg.EnvironmentVariables | Where-Object { $_.Value })
        $settings = Join-Path $script:RepoRoot "solution\build\$Target.settings.json"
        $cfg | ConvertTo-Json -Depth 5 | Set-Content $settings -Encoding utf8
    }
    $pacArgs += @('--settings-file', $settings)
}

Write-Host "Importing $(Split-Path $zip -Leaf) into $Target ($url)"
$out = pac @pacArgs 2>&1
$out | Write-Host
# pac can print an error and still exit 0.
if ($LASTEXITCODE -ne 0 -or ($out -match '^Error:')) { throw 'pac solution import failed' }
