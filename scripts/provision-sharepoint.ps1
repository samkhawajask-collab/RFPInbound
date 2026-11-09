<#
.SYNOPSIS
  Creates the target's SharePoint site (a Microsoft 365 group team site, ADR-0031) and its folders,
  then uploads the company content and the prepared templates. Idempotent.

.DESCRIPTION
  Uses Microsoft Graph with the signed-in az account (delegated Group.ReadWrite.All).
  Uploads ONLY 04_Company_Content/*.docx and assets/templates/* - never gold data, the dataset
  README or the generator source (hard rule 1, ARCHITECTURE.md 10.3).

.EXAMPLE
  ./scripts/provision-sharepoint.ps1
  ./scripts/provision-sharepoint.ps1 -Target test
#>
[CmdletBinding()]
param(
    [ValidateSet('dev', 'test')][string]$Target = 'dev',
    [switch]$SkipUploads
)

. (Join-Path $PSScriptRoot 'lib\Dataverse.ps1')
$res = Get-TargetResources -Target $Target
$alias = $res.SiteAlias
$name = $res.SiteName
$graph = 'https://graph.microsoft.com/v1.0'

function Invoke-Graph([string]$Method, [string]$Url, $Body, [string]$ContentType = 'application/json', [switch]$AllowNotFound) {
    $token = az account get-access-token --resource https://graph.microsoft.com --query accessToken -o tsv
    $p = @{ Method = $Method; Uri = $Url; Headers = @{ Authorization = "Bearer $token" } }
    if ($null -ne $Body) {
        if ($Body -is [byte[]]) { $p.Body = $Body } else { $p.Body = [Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Depth 10 -Compress)) }
        $p.ContentType = $ContentType
    }
    try { return Invoke-RestMethod @p }
    catch {
        $status = $_.Exception.PSObject.Properties['Response']?.Value.StatusCode.value__
        if ($AllowNotFound -and $status -eq 404) { return $null }
        $detail = if ($_.ErrorDetails) { $_.ErrorDetails.Message } else { $_.Exception.Message }
        throw "Graph $Method $Url failed ($status): $detail"
    }
}

# --- Group-backed team site ---------------------------------------------------------------
$group = (Invoke-Graph GET "$graph/groups?`$filter=mailNickname eq '$alias'&`$select=id,displayName").value | Select-Object -First 1
if (-not $group) {
    $me = Invoke-Graph GET "$graph/me?`$select=id"
    $group = Invoke-Graph POST "$graph/groups" @{
        displayName           = $name
        description           = "Proposal & RFP Response Factory - $Target document libraries"
        mailNickname          = $alias
        groupTypes            = @('Unified')
        mailEnabled           = $true
        securityEnabled       = $false
        visibility            = 'Private'
        'owners@odata.bind'   = @("$graph/users/$($me.id)")
        'members@odata.bind'  = @("$graph/users/$($me.id)")
    }
    Write-Host "  + Microsoft 365 group $name"
} else { Write-Host "  = Microsoft 365 group $name" }

# The team site is provisioned asynchronously after the group is created.
$site = $null
for ($i = 0; $i -lt 30 -and -not $site; $i++) {
    $site = Invoke-Graph GET "$graph/groups/$($group.id)/sites/root?`$select=id,webUrl" -AllowNotFound
    if (-not $site) { Start-Sleep -Seconds 10 }
}
if (-not $site) { throw 'Team site was not provisioned within 5 minutes; re-run later.' }
Write-Host "  site: $($site.webUrl)"

# --- Folders in the site's default document library ---------------------------------------
$drive = Invoke-Graph GET "$graph/groups/$($group.id)/drive?`$select=id,webUrl"
function Ensure-Folder([string]$Path) {
    $existing = Invoke-Graph GET "$graph/drives/$($drive.id)/root:/$Path`?`$select=id" -AllowNotFound
    if ($existing) { return }
    $parent = Split-Path $Path -Parent; $leaf = Split-Path $Path -Leaf
    $parentRef = if ($parent) { "root:/$($parent -replace '\\','/'):" } else { 'root' }
    Invoke-Graph POST "$graph/drives/$($drive.id)/$parentRef/children" @{ name = $leaf; folder = @{}; '@microsoft.graph.conflictBehavior' = 'fail' } | Out-Null
    Write-Host "  + folder $Path"
}
foreach ($f in 'Inbound', 'Content', 'Content/Company', 'Content/Templates', 'Output', 'Output/Draft', 'Output/Filed') { Ensure-Folder $f }

if ($SkipUploads) { return }

# --- Uploads: company content + prepared templates only ------------------------------------
function Send-File([string]$LocalPath, [string]$RemoteFolder) {
    $leaf = Split-Path $LocalPath -Leaf
    $bytes = [IO.File]::ReadAllBytes($LocalPath)
    Invoke-Graph PUT "$graph/drives/$($drive.id)/root:/$RemoteFolder/$([uri]::EscapeDataString($leaf)):/content" $bytes 'application/octet-stream' | Out-Null
    Write-Host "  ^ $RemoteFolder/$leaf"
}
$content = Join-Path $script:RepoRoot 'data\RFP_Synthetic_Dataset_Pilot\04_Company_Content'
Get-ChildItem $content -Filter '*.docx' | ForEach-Object { Send-File $_.FullName 'Content/Company' }
# Templates are used as supplied (ADR-0016): proposal, compliance matrix, clarification letter, approval card.
$templates = Join-Path $script:RepoRoot 'data\RFP_Synthetic_Dataset_Pilot\06_Templates'
foreach ($t in 'Proposal_Template.docx', 'Compliance_Matrix_Template.xlsx', 'Clarification_Letter_Template.docx', 'Approval_Card.json') {
    Send-File (Join-Path $templates $t) 'Content/Templates'
}
