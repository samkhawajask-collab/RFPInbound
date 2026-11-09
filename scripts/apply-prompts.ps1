<#
.SYNOPSIS
  Creates or updates the AI Builder prompts in solution/spec/prompts in dev (ADR-0015). Idempotent.

.DESCRIPTION
  Each spec becomes an msdyn_aimodel (GptPowerPrompt template) with a published run configuration:
    model -> training config (190690000) -> Train -> run config (190690001) -> PublishAIConfiguration.
  A prompt whose published configuration already matches the spec is left alone. A changed spec gets
  a new training/run configuration pair, because published prompt versions are immutable.
  Components join the RFPFactory solution. -Test runs each prompt once on its spec sample inputs.

.EXAMPLE
  ./scripts/apply-prompts.ps1 -Test
#>
[CmdletBinding()]
param(
    [ValidateSet('dev')][string]$Target = 'dev',
    [string[]]$Only,
    [switch]$Test
)

. (Join-Path $PSScriptRoot 'lib\Dataverse.ps1')
$inSolution = @{ 'MSCRM.SolutionUniqueName' = 'RFPFactory' }
$specDir = Join-Path $script:RepoRoot 'solution\spec\prompts'

function ConvertTo-PromptSegments([string]$text) {
    $segments = [System.Collections.Generic.List[object]]::new()
    $pos = 0
    foreach ($m in [regex]::Matches($text, '\{\{(\w+)\}\}')) {
        if ($m.Index -gt $pos) { $segments.Add([ordered]@{ type = 'literal'; text = $text.Substring($pos, $m.Index - $pos) }) }
        $segments.Add([ordered]@{ type = 'inputVariable'; id = $m.Groups[1].Value })
        $pos = $m.Index + $m.Length
    }
    if ($pos -lt $text.Length) { $segments.Add([ordered]@{ type = 'literal'; text = $text.Substring($pos) }) }
    return , $segments.ToArray()
}

function New-CustomConfiguration($spec) {
    $declared = @($spec.inputs | ForEach-Object id)
    $used = @([regex]::Matches($spec.instructions, '\{\{(\w+)\}\}') | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique)
    $missing = $used | Where-Object { $_ -notin $declared }
    if ($missing) { throw "$($spec.name): instructions use undeclared inputs: $($missing -join ', ')" }
    [ordered]@{
        version         = 'GptDynamicPrompt-2'
        prompt          = ConvertTo-PromptSegments $spec.instructions
        definitions     = [ordered]@{
            inputs   = @($spec.inputs | ForEach-Object { [ordered]@{ id = $_.id; text = $_.id; type = 'text'; quickTestValue = $_.sample } })
            formulas = @()
            data     = @()
            output   = [ordered]@{
                formats            = @('json')
                jsonSchema         = $spec.output
                jsonExamples       = @($spec.example)
                isCustomUserFormat = $true
            }
        }
        modelParameters = [ordered]@{ modelType = $spec.model; gptParameters = [ordered]@{ temperature = $spec.temperature } }
        settings        = [ordered]@{ recordRetrievalLimit = 30; shouldPreserveRecordLinks = $null; runtime = $null }
    }
}

# Canonical form for comparison: the parts the spec controls, serialized the same way.
function Get-Canonical($customJson) {
    $c = $customJson | ConvertFrom-Json -Depth 64
    [ordered]@{ prompt = $c.prompt; inputs = @($c.definitions.inputs | ForEach-Object { $_.id }); schema = $c.definitions.output.jsonSchema
        model = $c.modelParameters.modelType; temperature = $c.modelParameters.gptParameters.temperature } | ConvertTo-Json -Depth 64 -Compress
}

function Wait-Status([string]$configId, [int[]]$done, [int]$seconds = 300) {
    for ($i = 0; $i -lt $seconds / 5; $i++) {
        $c = Invoke-Dv -Path "msdyn_aiconfigurations($configId)?`$select=statuscode"
        if ($c.statuscode -in $done) { return $c.statuscode }
        Start-Sleep -Seconds 5
    }
    throw "Configuration $configId did not reach status $($done -join '/') in $seconds s"
}

function Publish-Prompt([string]$modelId, [string]$name, [string]$customJson, [int]$n) {
    $train = Invoke-Dv -Method POST -Path 'msdyn_aiconfigurations' -Headers ($inSolution + @{ Prefer = 'return=representation' }) -Body @{
        msdyn_name = "$name configuration $n"; msdyn_type = 190690000; msdyn_customconfiguration = $customJson
        'msdyn_AIModelId@odata.bind' = "/msdyn_aimodels($modelId)"
    }
    Invoke-Dv -Method POST -Path "msdyn_aiconfigurations($($train.msdyn_aiconfigurationid))/Microsoft.Dynamics.CRM.Train" -Body @{ version = '1.0' } | Out-Null
    if ((Wait-Status $train.msdyn_aiconfigurationid @(6, 9, 14)) -ne 6) { throw "$name training failed" }
    $run = Invoke-Dv -Method POST -Path 'msdyn_aiconfigurations' -Headers ($inSolution + @{ Prefer = 'return=representation' }) -Body @{
        msdyn_name = "$name run $n"; msdyn_type = 190690001; msdyn_customconfiguration = $customJson
        'msdyn_AIModelId@odata.bind' = "/msdyn_aimodels($modelId)"
        'msdyn_TrainedModelAIConfigurationPareId@odata.bind' = "/msdyn_aiconfigurations($($train.msdyn_aiconfigurationid))"
    }
    Invoke-Dv -Method POST -Path "msdyn_aiconfigurations($($run.msdyn_aiconfigurationid))/Microsoft.Dynamics.CRM.PublishAIConfiguration" -Body @{ version = '1.0' } | Out-Null
    if ((Wait-Status $run.msdyn_aiconfigurationid @(7, 10)) -ne 7) { throw "$name publish failed" }
}

Connect-Dataverse -Url (Resolve-TargetUrl -Target $Target)
$template = (Invoke-Dv -Path "msdyn_aitemplates?`$select=msdyn_aitemplateid&`$filter=msdyn_uniquename eq 'GptPowerPrompt'").value[0].msdyn_aitemplateid

foreach ($file in Get-ChildItem $specDir -Filter '*.json' | Sort-Object Name) {
    $spec = Get-Content $file.FullName -Raw | ConvertFrom-Json -Depth 64
    if ($Only -and $spec.name -notin $Only -and $file.BaseName -notin $Only) { continue }
    $customJson = New-CustomConfiguration $spec | ConvertTo-Json -Depth 64 -Compress
    $model = (Invoke-Dv -Path "msdyn_aimodels?`$select=msdyn_aimodelid,_msdyn_activerunconfigurationid_value&`$filter=msdyn_name eq '$($spec.name)'").value | Select-Object -First 1
    if (-not $model) {
        $model = Invoke-Dv -Method POST -Path 'msdyn_aimodels' -Headers ($inSolution + @{ Prefer = 'return=representation' }) -Body @{
            msdyn_name = $spec.name; 'msdyn_TemplateId@odata.bind' = "/msdyn_aitemplates($template)"
        }
        Publish-Prompt $model.msdyn_aimodelid $spec.name $customJson 1
        Write-Host "  + $($spec.name) published" -ForegroundColor Green
    } else {
        $active = $model._msdyn_activerunconfigurationid_value
        $current = if ($active) { (Invoke-Dv -Path "msdyn_aiconfigurations($active)?`$select=msdyn_customconfiguration").msdyn_customconfiguration }
        if ($current -and (Get-Canonical $current) -eq (Get-Canonical $customJson)) {
            Write-Host "  = $($spec.name)"
        } else {
            $n = @((Invoke-Dv -Path "msdyn_aiconfigurations?`$select=msdyn_aiconfigurationid&`$filter=_msdyn_aimodelid_value eq $($model.msdyn_aimodelid) and msdyn_type eq 190690000").value).Count + 1
            # Only one run configuration may be published at a time: unpublish the active one first
            # (UnpublishAIConfiguration, confirmed in the environment's $metadata 2026-10-29).
            $published = @((Invoke-Dv -Path "msdyn_aiconfigurations?`$select=msdyn_aiconfigurationid&`$filter=_msdyn_aimodelid_value eq $($model.msdyn_aimodelid) and msdyn_type eq 190690001 and statuscode eq 7").value)
            foreach ($old in $published) {
                Invoke-Dv -Method POST -Path "msdyn_aiconfigurations($($old.msdyn_aiconfigurationid))/Microsoft.Dynamics.CRM.UnpublishAIConfiguration" -Body @{ version = '1.0' } | Out-Null
                for ($i = 0; $i -lt 60 -and (Invoke-Dv -Path "msdyn_aiconfigurations($($old.msdyn_aiconfigurationid))?`$select=statuscode").statuscode -ne 0; $i++) { Start-Sleep -Seconds 5 }  # 0 = unpublished draft
            }
            Publish-Prompt $model.msdyn_aimodelid $spec.name $customJson $n
            Write-Host "  ~ $($spec.name) republished (configuration $n)" -ForegroundColor Yellow
        }
    }

    if ($Test) {
        $modelId = (Invoke-Dv -Path "msdyn_aimodels?`$select=msdyn_aimodelid&`$filter=msdyn_name eq '$($spec.name)'").value[0].msdyn_aimodelid
        $request = @{ '@odata.type' = 'Microsoft.Dynamics.CRM.expando' }
        foreach ($i in $spec.inputs) { $request[$i.id] = $i.sample }
        # Predict requires a source descriptor; this is the shape the Power Automate connector sends.
        $source = '{"consumptionSource":"PowerAutomate","partnerSource":"AIBuilder","consumptionSourceVersion":"1"}'
        $r = Invoke-Dv -Method POST -Path "msdyn_aimodels($modelId)/Microsoft.Dynamics.CRM.Predict" -Body @{ version = '2.0'; requestv2 = $request; source = $source }
        $out = $r.responsev2.predictionOutput
        Write-Host ("    test: {0} credits, {1} tokens -> {2}" -f $out.costAsCopilotCredits, $out.totalTokens, ($out.text -replace '\s+', ' ').Substring(0, [Math]::Min(160, ($out.text -replace '\s+', ' ').Length)))
    }
}
