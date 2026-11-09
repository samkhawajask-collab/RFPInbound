<#
.SYNOPSIS
  Applies solution/spec/tables.json to the dev environment (ADR-0014).

.DESCRIPTION
  Idempotent: creates whatever is missing (publisher, solution, global choices, tables, columns,
  lookups, alternate keys, environment variable definitions, connection references) and reports
  drift on what already exists. It never deletes or retypes anything. Every component is created
  with MSCRM.SolutionUniqueName so it joins the RFPFactory solution.

  Dev only: schema changes are made in dev and reach test as a managed import.
  After a run, reconcile the source with scripts/sync-solution.ps1.

.EXAMPLE
  ./scripts/apply-schema.ps1
#>
[CmdletBinding()]
param(
    [ValidateSet('dev')][string]$Target = 'dev'
)

. (Join-Path $PSScriptRoot 'lib\Dataverse.ps1')

$spec = Get-Content (Join-Path $script:RepoRoot 'solution\spec\tables.json') -Raw | ConvertFrom-Json -AsHashtable
$solutionName = $spec.solution.uniquename
$inSolution = @{ 'MSCRM.SolutionUniqueName' = $solutionName }
$stats = [ordered]@{ created = 0; existing = 0; drift = 0 }

function Write-Step([string]$state, [string]$what) {
    switch ($state) {
        'created' { $stats.created++; Write-Host "  + $what" -ForegroundColor Green }
        'existing' { $stats.existing++; Write-Verbose "  = $what" }
        'drift' { $stats.drift++; Write-Warning "  ! $what" }
    }
}

function Get-Filtered([string]$set, [string]$filter, [string]$select) {
    (Invoke-Dv -Path "$set`?`$select=$select&`$filter=$([uri]::EscapeDataString($filter))").value
}

Connect-Dataverse -Url (Resolve-TargetUrl -Target $Target)
Write-Host "Applying schema to $script:DvUrl ($Target)"

# --- Publisher and solution ---------------------------------------------------------------
$pub = $spec.publisher
$existingPub = Get-Filtered 'publishers' "uniquename eq '$($pub.uniquename)'" 'publisherid,customizationprefix,customizationoptionvalueprefix'
if (-not $existingPub) {
    Invoke-Dv -Method POST -Path 'publishers' -Body $pub | Out-Null
    $existingPub = Get-Filtered 'publishers' "uniquename eq '$($pub.uniquename)'" 'publisherid,customizationprefix,customizationoptionvalueprefix'
    Write-Step created "publisher $($pub.uniquename)"
} else {
    Write-Step existing "publisher $($pub.uniquename)"
    if ($existingPub.customizationprefix -ne $pub.customizationprefix -or $existingPub.customizationoptionvalueprefix -ne $pub.customizationoptionvalueprefix) {
        Write-Step drift "publisher $($pub.uniquename) prefix differs from spec"
    }
}

$sol = $spec.solution
if (-not (Get-Filtered 'solutions' "uniquename eq '$solutionName'" 'solutionid')) {
    $body = @{
        uniquename               = $sol.uniquename
        friendlyname             = $sol.friendlyname
        version                  = $sol.version
        description              = $sol.description
        'publisherid@odata.bind' = "/publishers($($existingPub.publisherid))"
    }
    Invoke-Dv -Method POST -Path 'solutions' -Body $body | Out-Null
    Write-Step created "solution $solutionName"
} else { Write-Step existing "solution $solutionName" }

# --- Global choices -----------------------------------------------------------------------
$choiceIds = @{}
foreach ($name in ($spec.choices.Keys | Sort-Object)) {
    $choice = $spec.choices[$name]
    $existing = Invoke-Dv -Path "GlobalOptionSetDefinitions(Name='$name')" -AllowNotFound
    if (-not $existing) {
        $body = @{
            '@odata.type' = 'Microsoft.Dynamics.CRM.OptionSetMetadata'
            Name          = $name
            DisplayName   = New-DvLabel $choice.label
            IsGlobal      = $true
            OptionSetType = 'Picklist'
            Options       = @($choice.options | ForEach-Object { @{ Value = [int]$_[0]; Label = New-DvLabel $_[1] } })
        }
        Invoke-Dv -Method POST -Path 'GlobalOptionSetDefinitions' -Body $body -Headers $inSolution | Out-Null
        $existing = Invoke-Dv -Path "GlobalOptionSetDefinitions(Name='$name')"
        Write-Step created "choice $name"
    } else {
        Write-Step existing "choice $name"
        $have = @($existing.Options | ForEach-Object { [int]$_.Value })
        foreach ($opt in $choice.options) {
            if ([int]$opt[0] -notin $have) {
                Invoke-Dv -Method POST -Path 'InsertOptionValue' -Body @{
                    OptionSetName = $name; Value = [int]$opt[0]; Label = New-DvLabel $opt[1]; SolutionUniqueName = $solutionName
                } | Out-Null
                Write-Step created "choice $name option $($opt[0]) $($opt[1])"
            }
        }
    }
    $choiceIds[$name] = $existing.MetadataId
}

# --- Tables (with primary name column) ----------------------------------------------------
foreach ($t in $spec.tables) {
    if (Invoke-Dv -Path "EntityDefinitions(LogicalName='$($t.name)')?`$select=LogicalName" -AllowNotFound) {
        Write-Step existing "table $($t.name)"
        continue
    }
    $pn = $t.primaryName
    $body = @{
        '@odata.type'         = 'Microsoft.Dynamics.CRM.EntityMetadata'
        SchemaName            = $t.name
        DisplayName           = New-DvLabel $t.label
        DisplayCollectionName = New-DvLabel $t.plural
        OwnershipType         = $t.ownership
        IsActivity            = $false
        HasActivities         = $false
        HasNotes              = $false
        PrimaryNameAttribute  = $pn.name
        Attributes            = @(@{
                '@odata.type' = 'Microsoft.Dynamics.CRM.StringAttributeMetadata'
                SchemaName    = $pn.name
                IsPrimaryName = $true
                RequiredLevel = @{ Value = 'ApplicationRequired' }
                MaxLength     = $pn.maxLength
                FormatName    = @{ Value = 'Text' }
                DisplayName   = New-DvLabel $pn.label
            })
    }
    Invoke-Dv -Method POST -Path 'EntityDefinitions' -Body $body -Headers $inSolution | Out-Null
    Write-Step created "table $($t.name)"
}

# --- Columns (lookups last, once every target table exists) -------------------------------
$odataType = @{
    text = 'StringAttributeMetadata'; email = 'StringAttributeMetadata'; memo = 'MemoAttributeMetadata'
    int = 'IntegerAttributeMetadata'; decimal = 'DecimalAttributeMetadata'; date = 'DateTimeAttributeMetadata'
    datetime = 'DateTimeAttributeMetadata'; bool = 'BooleanAttributeMetadata'; choice = 'PicklistAttributeMetadata'
    file = 'FileAttributeMetadata'; lookup = 'LookupAttributeMetadata'
}

function New-ColumnBody($c) {
    $required = if ($c['required']) { 'ApplicationRequired' } else { 'None' }
    $b = @{
        '@odata.type' = "Microsoft.Dynamics.CRM.$($odataType[$c.type])"
        SchemaName    = $c.name
        DisplayName   = New-DvLabel $c.label
        RequiredLevel = @{ Value = $required }
    }
    switch ($c.type) {
        'text' { $b.MaxLength = $c.maxLength; $b.FormatName = @{ Value = 'Text' } }
        'email' { $b.MaxLength = $c.maxLength; $b.FormatName = @{ Value = 'Email' } }
        'memo' { $b.MaxLength = $c.maxLength; $b.Format = 'TextArea' }
        'int' { $b.Format = 'None'; $b.MinValue = $c.min; $b.MaxValue = $c.max }
        'decimal' { $b.Precision = $c.precision; $b.MinValue = $c.min; $b.MaxValue = $c.max }
        'date' { $b.Format = 'DateOnly'; $b.DateTimeBehavior = @{ Value = 'DateOnly' } }
        'datetime' { $b.Format = 'DateAndTime'; $b.DateTimeBehavior = @{ Value = 'UserLocal' } }
        'bool' {
            $b.DefaultValue = $false
            $b.OptionSet = @{
                '@odata.type' = 'Microsoft.Dynamics.CRM.BooleanOptionSetMetadata'
                TrueOption    = @{ Value = 1; Label = New-DvLabel 'Yes' }
                FalseOption   = @{ Value = 0; Label = New-DvLabel 'No' }
            }
        }
        'choice' { $b['GlobalOptionSet@odata.bind'] = "/GlobalOptionSetDefinitions($($choiceIds[$c.choice]))" }
        'file' { $b.MaxSizeInKB = $c.maxSizeInKB }
    }
    return $b
}

foreach ($pass in 'plain', 'lookup') {
    foreach ($t in $spec.tables) {
        foreach ($c in $t.columns) {
            if (($c.type -eq 'lookup') -ne ($pass -eq 'lookup')) { continue }
            $existing = Invoke-Dv -Path "EntityDefinitions(LogicalName='$($t.name)')/Attributes(LogicalName='$($c.name)')?`$select=LogicalName,AttributeType" -AllowNotFound
            if ($existing) {
                $expected = $odataType[$c.type] -replace 'AttributeMetadata$', ''
                $map = @{ String = 'String'; Memo = 'Memo'; Integer = 'Integer'; Decimal = 'Decimal'; DateTime = 'DateTime'; Boolean = 'Boolean'; Picklist = 'Picklist'; File = 'File'; Lookup = 'Lookup' }
                if ($existing.AttributeType -ne $map[$expected] -and -not ($expected -eq 'File' -and $existing.AttributeType -eq 'Virtual')) {
                    Write-Step drift "$($t.name).$($c.name) is $($existing.AttributeType), spec says $($c.type)"
                } else { Write-Step existing "$($t.name).$($c.name)" }
                continue
            }
            if ($c.type -eq 'lookup') {
                $body = @{
                    '@odata.type'        = 'Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata'
                    SchemaName           = "$($t.name)_$($c.name)"
                    ReferencedEntity     = $c.target
                    ReferencedAttribute  = "$($c.target)id"
                    ReferencingEntity    = $t.name
                    CascadeConfiguration = @{ Assign = 'NoCascade'; Delete = 'RemoveLink'; Merge = 'NoCascade'; Reparent = 'NoCascade'; Share = 'NoCascade'; Unshare = 'NoCascade' }
                    Lookup               = New-ColumnBody $c
                }
                Invoke-Dv -Method POST -Path 'RelationshipDefinitions' -Body $body -Headers $inSolution | Out-Null
            } else {
                Invoke-Dv -Method POST -Path "EntityDefinitions(LogicalName='$($t.name)')/Attributes" -Body (New-ColumnBody $c) -Headers $inSolution | Out-Null
            }
            Write-Step created "$($t.name).$($c.name) ($($c.type))"
        }
    }
}

# --- Alternate keys -----------------------------------------------------------------------
foreach ($t in $spec.tables) {
    if (-not $t.ContainsKey('keys')) { continue }
    $have = @((Invoke-Dv -Path "EntityDefinitions(LogicalName='$($t.name)')/Keys?`$select=SchemaName,KeyAttributes").value |
            ForEach-Object { ($_.KeyAttributes | Sort-Object) -join ',' })
    $i = 0
    foreach ($key in $t['keys']) {  # not $t.keys: that is the hashtable's own Keys property
        $i++
        $sig = ($key | Sort-Object) -join ','
        if ($sig -in $have) { Write-Step existing "key $($t.name)($sig)"; continue }
        $body = @{
            SchemaName    = "$($t.name)_key$i"
            DisplayName   = New-DvLabel "$($t.label) key"
            KeyAttributes = @($key)
        }
        Invoke-Dv -Method POST -Path "EntityDefinitions(LogicalName='$($t.name)')/Keys" -Body $body -Headers $inSolution | Out-Null
        Write-Step created "key $($t.name)($sig)"
    }
}

# --- Environment variable definitions (no values: those are per environment) --------------
$envVarType = @{ String = 100000000; Number = 100000001; Boolean = 100000002; JSON = 100000003 }
foreach ($ev in $spec.environmentVariables) {
    $existingEv = Get-Filtered 'environmentvariabledefinitions' "schemaname eq '$($ev.name)'" 'environmentvariabledefinitionid,displayname,description'
    if ($existingEv) {
        # Labels and descriptions are safe to bring in line with the spec; type and schema name are not.
        if ($existingEv.displayname -ne $ev.label -or $existingEv.description -ne $ev.description) {
            Invoke-Dv -Method PATCH -Path "environmentvariabledefinitions($($existingEv.environmentvariabledefinitionid))" -Headers ($inSolution + @{ 'If-Match' = '*' }) -Body @{ displayname = $ev.label; description = $ev.description } | Out-Null
            Write-Step created "environment variable $($ev.name) label/description updated"
        } else { Write-Step existing "environment variable $($ev.name)" }
        continue
    }
    $body = @{ schemaname = $ev.name; displayname = $ev.label; description = $ev.description; type = $envVarType[$ev.type] }
    if ($ev['default']) { $body.defaultvalue = $ev.default }
    Invoke-Dv -Method POST -Path 'environmentvariabledefinitions' -Body $body -Headers $inSolution | Out-Null
    Write-Step created "environment variable $($ev.name)"
}

# --- Connection references (unbound; connections are bound per environment at import) -----
foreach ($cr in $spec.connectionReferences) {
    if (Get-Filtered 'connectionreferences' "connectionreferencelogicalname eq '$($cr.name.ToLower())'" 'connectionreferenceid') {
        Write-Step existing "connection reference $($cr.name)"; continue
    }
    $apiName = $cr['connector']
    $custom = $null
    if ($cr['customConnector']) {
        # Custom connectors are addressed by their environment-assigned internal ID.
        $custom = (Invoke-Dv -Path "connectors?`$select=connectorid,connectorinternalid&`$filter=name eq '$($cr.customConnector)'").value | Select-Object -First 1
        if (-not $custom) { Write-Step drift "connection reference $($cr.name): custom connector $($cr.customConnector) not found; run apply-connector.ps1 first"; continue }
        $apiName = $custom.connectorinternalid
    }
    $body = @{
        connectionreferencelogicalname = $cr.name.ToLower()
        connectionreferencedisplayname = $cr.label
        connectorid                    = "/providers/Microsoft.PowerApps/apis/$apiName"
    }
    # Without the customconnectorid lookup, solution export refuses the reference ("requires the custom
    # connector to be added to a dataverse solution") even when the connector is in the solution.
    if ($custom) { $body['CustomConnectorId@odata.bind'] = "/connectors($($custom.connectorid))" }
    Invoke-Dv -Method POST -Path 'connectionreferences' -Body $body -Headers $inSolution | Out-Null
    Write-Step created "connection reference $($cr.name)"
}

# --- Publish only this solution's tables and choices (dev is shared; never PublishAllXml) -
if ($stats.created -gt 0) {
    $entities = ($spec.tables | ForEach-Object { "<entity>$($_.name)</entity>" }) -join ''
    $optionsets = ($spec.choices.Keys | Sort-Object | ForEach-Object { "<optionset>$_</optionset>" }) -join ''
    Invoke-Dv -Method POST -Path 'PublishXml' -Body @{
        ParameterXml = "<importexportxml><entities>$entities</entities><optionsets>$optionsets</optionsets></importexportxml>"
    } | Out-Null
    Write-Host '  published tables and choices'
}

Write-Host ("Done: {0} created, {1} already present, {2} drift warnings" -f $stats.created, $stats.existing, $stats.drift)
if ($stats.drift -gt 0) { exit 2 }
