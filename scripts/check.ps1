<#
.SYNOPSIS
  Runs Power Apps solution checker on the packed solution. Run before every import.

.DESCRIPTION
  Fails (exit 1) when the checker reports any Critical or High issue. Results land in
  solution/build/check/ (git-ignored with the rest of solution/build).

.EXAMPLE
  ./scripts/check.ps1
#>
[CmdletBinding()]
param(
    [string]$ZipFile
)
. (Join-Path $PSScriptRoot 'lib\Dataverse.ps1')

if (-not $ZipFile) { $ZipFile = Join-Path $script:RepoRoot 'solution\build\RFPFactory.zip' }
$out = Join-Path $script:RepoRoot 'solution\build\check'
Remove-Item -Recurse -Force $out -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $out | Out-Null

$output = pac solution check --path $ZipFile --outputDirectory $out --geo UnitedStates --environment (Resolve-TargetUrl -Target dev) 2>&1
$output | Write-Host
if ($LASTEXITCODE -ne 0) { throw 'pac solution check failed to run' }

# pac prints a header row "Critical High Medium Low Informational" followed by a row of five counts.
$lines = @($output | ForEach-Object { "$_" })
$header = [array]::FindIndex([string[]]$lines, [Predicate[string]] { param($l) $l -match 'Critical\s+High\s+Medium\s+Low\s+Informational' })
if ($header -lt 0) { throw "Could not find the checker summary in pac output; see $out" }
$countsLine = $lines[($header + 1)..($lines.Count - 1)] | Where-Object { $_ -match '^\s*\d+(\s+\d+){4}\s*$' } | Select-Object -First 1
if (-not $countsLine) { throw "Could not read the checker counts; see $out" }
$counts = [int[]]($countsLine.Trim() -split '\s+')
$critical, $high = $counts[0], $counts[1]
Write-Host ("Checker counts: Critical {0}, High {1}, Medium {2}, Low {3}, Informational {4}" -f [object[]]$counts)
if ($critical + $high -gt 0) {
    Write-Error "Solution checker found $critical critical and $high high issues; see $out"
    exit 1
}
Write-Host 'Solution checker: no critical or high issues'
