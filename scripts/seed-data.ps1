<#
.SYNOPSIS
  Seeds the 11 reference tables from data/RFP_Synthetic_Dataset_Pilot/09_Dataverse_Import.

.DESCRIPTION
  Idempotent upsert keyed on each table's business key (ADR-0014): find by key, update if present,
  create if not. Runs in two passes - scalar columns first, then lookups - because the CSVs
  reference each other out of file order (01 category -> 02 sme) and hrs_answer references itself.
  Finishes by checking row counts against the CSVs and exits non-zero on any mismatch.

  Never reads gold data: only the eleven files listed below are opened (hard rule 1).

.EXAMPLE
  ./scripts/seed-data.ps1 -Target dev
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('dev', 'test')][string]$Target
)

. (Join-Path $PSScriptRoot 'lib\Dataverse.ps1')

$importDir = Join-Path $script:RepoRoot 'data\RFP_Synthetic_Dataset_Pilot\09_Dataverse_Import'
$files = [ordered]@{
    hrs_category      = '01_hrs_category.csv'
    hrs_sme           = '02_hrs_sme.csv'
    hrs_answer        = '03_hrs_answer.csv'
    hrs_policyrule    = '04_hrs_policyrule.csv'
    hrs_approver      = '05_hrs_approver.csv'
    hrs_opportunity   = '06_hrs_opportunity.csv'
    hrs_certification = '07_hrs_certification.csv'
    hrs_insurance     = '08_hrs_insurance.csv'
    hrs_casestudy     = '09_hrs_casestudy.csv'
    hrs_teammember    = '10_hrs_teammember.csv'
    hrs_pastproposal  = '11_hrs_pastproposal.csv'
}

$spec = Get-Content (Join-Path $script:RepoRoot 'solution\spec\tables.json') -Raw | ConvertFrom-Json -AsHashtable
$tables = @{}; foreach ($t in $spec.tables) { $tables[$t.name] = $t }

Connect-Dataverse -Url (Resolve-TargetUrl -Target $Target)
Write-Host "Seeding reference data into $script:DvUrl ($Target)"

# Entity set names and lookup navigation properties come from metadata, not guesses.
$meta = @{}
foreach ($name in $files.Keys) {
    $e = Invoke-Dv -Path "EntityDefinitions(LogicalName='$name')?`$select=EntitySetName,PrimaryIdAttribute&`$expand=ManyToOneRelationships(`$select=ReferencingAttribute,ReferencingEntityNavigationPropertyName)"
    $nav = @{}; foreach ($r in $e.ManyToOneRelationships) { $nav[$r.ReferencingAttribute] = $r.ReferencingEntityNavigationPropertyName }
    $meta[$name] = @{ set = $e.EntitySetName; id = $e.PrimaryIdAttribute; nav = $nav }
}

function ConvertTo-ODataLiteral([string]$s) { "'" + $s.Replace("'", "''") + "'" }

# Column in Dataverse for a CSV header (the primary-name column may carry a different name - see ADR-0014).
function Get-ColumnMap($t) {
    $map = [ordered]@{}
    $pn = $t.primaryName
    $csvKey = if ($pn['csv']) { $pn.csv } else { $pn.name }
    $map[$csvKey] = @{ name = $pn.name; type = 'text' }
    # Columns fed from data/supplement are seeded by their own pass, so the dataset pass never nulls them.
    foreach ($c in $t.columns) { if (-not $c['supplement']) { $map[$c.name] = $c } }
    return $map
}

function Find-RowId([string]$table, [string]$keyColumn, [string]$keyValue) {
    $m = $meta[$table]
    $filter = [uri]::EscapeDataString("$keyColumn eq $(ConvertTo-ODataLiteral $keyValue)")
    $rows = (Invoke-Dv -Path "$($m.set)?`$select=$($m.id)&`$filter=$filter").value
    if ($rows.Count -gt 1) { throw "$table has $($rows.Count) rows with $keyColumn = $keyValue" }
    if ($rows) { return $rows[0].($m.id) }
    return $null
}

$idCache = @{}
function Resolve-Lookup([string]$table, [string]$keyValue) {
    $ck = "$table|$keyValue"
    if (-not $idCache.ContainsKey($ck)) {
        $id = Find-RowId $table $tables[$table].primaryName.name $keyValue
        if (-not $id) { throw "Lookup target not found: $table '$keyValue'" }
        $idCache[$ck] = $id
    }
    return $idCache[$ck]
}

function Convert-Value($col, [string]$raw) {
    if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
    switch ($col.type) {
        'int' { return [int]$raw }
        'decimal' { return [decimal]::Parse($raw, [Globalization.CultureInfo]::InvariantCulture) }
        'bool' { return $raw -in 'true', 'yes', '1' }
        'choice' {
            $opt = $spec.choices[$col.choice].options | Where-Object { $_[1] -eq $raw.Trim() }
            if (-not $opt) { throw "Value '$raw' is not an option of $($col.choice)" }
            return [int]$opt[0]
        }
        default { return $raw }
    }
}

$stats = [ordered]@{ created = 0; updated = 0 }
foreach ($pass in 'scalar', 'lookup') {
    foreach ($table in $files.Keys) {
        $t = $tables[$table]; $m = $meta[$table]
        $map = Get-ColumnMap $t
        $keyCsv = @($map.Keys)[0]
        $rows = Import-Csv (Join-Path $importDir $files[$table]) -Encoding utf8
        foreach ($row in $rows) {
            $body = @{}
            foreach ($csvCol in $map.Keys) {
                $col = $map[$csvCol]
                $raw = $row.$csvCol
                if ($pass -eq 'scalar' -and $col.type -ne 'lookup') {
                    $body[$col.name] = Convert-Value $col $raw
                } elseif ($pass -eq 'lookup' -and $col.type -eq 'lookup') {
                    $nav = $m.nav[$col.name]
                    if ([string]::IsNullOrWhiteSpace($raw)) { continue }
                    $targetId = Resolve-Lookup $col.target $raw.Trim()
                    $body["$nav@odata.bind"] = "/$($meta[$col.target].set)($targetId)"
                }
            }
            if ($body.Count -eq 0) { continue }
            $key = $row.$keyCsv
            $id = if ($idCache.ContainsKey("$table|$key")) { $idCache["$table|$key"] } else { Find-RowId $table $t.primaryName.name $key }
            if ($id) {
                Invoke-Dv -Method PATCH -Path "$($m.set)($id)" -Body $body -Headers @{ 'If-Match' = '*' } | Out-Null
                if ($pass -eq 'scalar') { $stats.updated++ }
            } else {
                $created = Invoke-Dv -Method POST -Path $m.set -Body $body -Headers @{ Prefer = 'return=representation' }
                $id = $created.($m.id)
                $stats.created++
            }
            $idCache["$table|$key"] = $id
        }
        Write-Host ("  {0,-18} {1} pass: {2} rows" -f $table, $pass, $rows.Count)
    }
}

# --- Supplement pass: columns sourced from data/supplement (ADR-0017) ----------------------
$supplementDir = Join-Path $script:RepoRoot 'data\supplement'
foreach ($table in $files.Keys) {
    $t = $tables[$table]; $m = $meta[$table]
    $bySource = $t.columns | Where-Object { $_['supplement'] } | Group-Object { $_['supplement'] }
    foreach ($group in $bySource) {
        $csvPath = Join-Path $supplementDir $group.Name
        $keyCsv = $t.primaryName['csv'] ?? $t.primaryName.name
        $rows = Import-Csv $csvPath -Encoding utf8
        foreach ($row in $rows) {
            $id = Find-RowId $table $t.primaryName.name $row.$keyCsv
            if (-not $id) { throw "$($group.Name): no $table row with key '$($row.$keyCsv)'" }
            $body = @{}
            foreach ($c in $group.Group) { $body[$c.name] = Convert-Value $c $row.($c.name) }
            Invoke-Dv -Method PATCH -Path "$($m.set)($id)" -Body $body -Headers @{ 'If-Match' = '*' } | Out-Null
        }
        Write-Host ("  {0,-18} supplement {1}: {2} rows" -f $table, $group.Name, $rows.Count)
    }
}

# --- Verify row counts against the CSVs ---------------------------------------------------
$failed = $false
Write-Host 'Row counts (Dataverse / CSV):'
foreach ($table in $files.Keys) {
    $expected = (Import-Csv (Join-Path $importDir $files[$table]) -Encoding utf8).Count
    $actual = (Invoke-Dv -Path "$($meta[$table].set)?`$select=$($meta[$table].id)&`$count=true").'@odata.count'
    $ok = $actual -eq $expected
    if (-not $ok) { $failed = $true }
    Write-Host ("  {0,-18} {1,4} / {2,-4} {3}" -f $table, $actual, $expected, $(if ($ok) { 'OK' } else { 'MISMATCH' }))
}
Write-Host ("Done: {0} created, {1} updated" -f $stats.created, $stats.updated)
if ($failed) { exit 1 }
