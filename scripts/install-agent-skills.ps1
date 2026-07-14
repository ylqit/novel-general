<#
.SYNOPSIS
Installs the bundled longform-novel-engine Codex and Claude Code skills.

.DESCRIPTION
The installer copies or junctions only the skill package directories required by
Codex / Claude Code:

- longform-novel-codex/
- longform-novel-claude/
- shared/

It does not copy novel projects, manuscripts, runtime databases, model caches,
environment files, or API keys.

.EXAMPLE
.\scripts\install-agent-skills.ps1 -Tool all -Mode copy

.EXAMPLE
.\scripts\install-agent-skills.ps1 -Tool codex -Mode junction -Force
#>

[CmdletBinding()]
param(
    [ValidateSet("codex", "claude-code", "all")]
    [string]$Tool = "all",

    [ValidateSet("copy", "junction")]
    [string]$Mode = "copy",

    [switch]$Force,

    [string]$CodexSkillRoot = (Join-Path $env:USERPROFILE ".codex\skills"),

    [string]$ClaudeSkillRoot = (Join-Path $env:USERPROFILE ".claude\skills")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Convert-Path (Join-Path $ScriptDir "..")

function Convert-ToFullPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "Path must not be empty."
    }

    $expanded = [Environment]::ExpandEnvironmentVariables($Path)
    if (-not [System.IO.Path]::IsPathRooted($expanded)) {
        $expanded = Join-Path (Get-Location) $expanded
    }

    $full = [System.IO.Path]::GetFullPath($expanded)
    $root = [System.IO.Path]::GetPathRoot($full)
    while ($full.Length -gt $root.Length -and ($full.EndsWith("\") -or $full.EndsWith("/"))) {
        $full = $full.Substring(0, $full.Length - 1)
    }
    return $full
}

function Test-IsSamePath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Left,

        [Parameter(Mandatory = $true)]
        [string]$Right
    )

    return [string]::Equals((Convert-ToFullPath $Left), (Convert-ToFullPath $Right), [StringComparison]::OrdinalIgnoreCase)
}

function Test-IsChildPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Child,

        [Parameter(Mandatory = $true)]
        [string]$Parent
    )

    $childFull = Convert-ToFullPath $Child
    $parentFull = Convert-ToFullPath $Parent
    $separator = [System.IO.Path]::DirectorySeparatorChar
    $prefix = $parentFull
    if (-not $prefix.EndsWith("$separator")) {
        $prefix = "$prefix$separator"
    }
    return $childFull.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
}

function Assert-NotDangerousPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Role
    )

    $full = Convert-ToFullPath $Path
    $pathRoot = [System.IO.Path]::GetPathRoot($full)
    $homePath = Convert-ToFullPath $HOME
    $repoPath = Convert-ToFullPath $RepoRoot

    if ([string]::IsNullOrWhiteSpace($full)) {
        throw "$Role path must not be empty."
    }
    if ([string]::Equals($full, $pathRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Role path is a filesystem root and is not allowed: $full"
    }
    if ([string]::Equals($full, $homePath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Role path is the user home directory and is not allowed: $full"
    }
    if ([string]::Equals($full, $repoPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Role path is the repository root and is not allowed: $full"
    }
}

function Assert-SafeSkillRoot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SkillRoot
    )

    Assert-NotDangerousPath -Path $SkillRoot -Role "Skill root"
}

function Assert-SafeInstallTarget {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Target,

        [Parameter(Mandatory = $true)]
        [string]$SkillRoot
    )

    Assert-NotDangerousPath -Path $Target -Role "Install target"

    if (Test-IsSamePath -Left $Target -Right $SkillRoot) {
        throw "Install target must not be the skill root itself: $(Convert-ToFullPath $Target)"
    }

    if (-not (Test-IsChildPath -Child $Target -Parent $SkillRoot)) {
        throw "Install target must stay inside the selected skill root. target=$(Convert-ToFullPath $Target) root=$(Convert-ToFullPath $SkillRoot)"
    }
}

function Remove-ExistingTarget {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Target,

        [Parameter(Mandatory = $true)]
        [string]$SkillRoot
    )

    Assert-SafeInstallTarget -Target $Target -SkillRoot $SkillRoot
    if (-not (Test-Path -LiteralPath $Target)) {
        return
    }

    if (-not $Force) {
        throw "Target already exists: $Target. Re-run with -Force to replace it."
    }

    Remove-Item -LiteralPath $Target -Recurse -Force
}

function Copy-DirectoryContents {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,

        [Parameter(Mandatory = $true)]
        [string]$Target
    )

    New-Item -ItemType Directory -Force -Path $Target | Out-Null
    Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $Target -Recurse -Force
    }
}

function Install-PackageDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,

        [Parameter(Mandatory = $true)]
        [string]$Source,

        [Parameter(Mandatory = $true)]
        [string]$Target,

        [Parameter(Mandatory = $true)]
        [string]$SkillRoot
    )

    $sourceFull = Convert-ToFullPath $Source
    $rootFull = Convert-ToFullPath $SkillRoot
    $targetFull = Convert-ToFullPath $Target

    if (-not (Test-Path -LiteralPath $sourceFull -PathType Container)) {
        throw "Source package not found: $sourceFull"
    }

    Assert-SafeSkillRoot -SkillRoot $rootFull
    Assert-SafeInstallTarget -Target $targetFull -SkillRoot $rootFull
    New-Item -ItemType Directory -Force -Path $rootFull | Out-Null

    if ($Mode -eq "copy") {
        if ((Test-Path -LiteralPath $targetFull) -and ((Get-Item -LiteralPath $targetFull).Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            Remove-ExistingTarget -Target $targetFull -SkillRoot $rootFull
        }
        Copy-DirectoryContents -Source $sourceFull -Target $targetFull
    }
    else {
        Remove-ExistingTarget -Target $targetFull -SkillRoot $rootFull
        New-Item -ItemType Junction -Path $targetFull -Target $sourceFull | Out-Null
    }

    [pscustomobject]@{
        Label = $Label
        Mode = $Mode
        Source = $sourceFull
        Target = $targetFull
    }
}

$sharedSource = Join-Path $RepoRoot "shared"
$packages = @{
    "codex" = @{
        Label = "Codex skill"
        SkillName = "longform-novel-codex"
        Source = Join-Path $RepoRoot "longform-novel-codex"
        SkillRoot = $CodexSkillRoot
    }
    "claude-code" = @{
        Label = "Claude Code skill"
        SkillName = "longform-novel-claude"
        Source = Join-Path $RepoRoot "longform-novel-claude"
        SkillRoot = $ClaudeSkillRoot
    }
}

if ($Tool -eq "all") {
    $selectedTools = @("codex", "claude-code")
}
else {
    $selectedTools = @($Tool)
}

$installed = New-Object System.Collections.Generic.List[object]
$installedSharedRoots = @{}

foreach ($selectedTool in $selectedTools) {
    $package = $packages[$selectedTool]
    $skillRoot = Convert-ToFullPath $package["SkillRoot"]

    if (-not $installedSharedRoots.ContainsKey($skillRoot)) {
        $sharedTarget = Join-Path $skillRoot "shared"
        $installed.Add((Install-PackageDirectory -Label "Shared skill references" -Source $sharedSource -Target $sharedTarget -SkillRoot $skillRoot))
        $installedSharedRoots[$skillRoot] = $true
    }

    $skillTarget = Join-Path $skillRoot $package["SkillName"]
    $installed.Add((Install-PackageDirectory -Label $package["Label"] -Source $package["Source"] -Target $skillTarget -SkillRoot $skillRoot))
}

Write-Host "OK: longform-novel-engine agent skills installed"
Write-Host "tool=$Tool"
Write-Host "mode=$Mode"
foreach ($item in $installed) {
    Write-Host ("- {0}: {1}" -f $item.Label, $item.Target)
}
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Restart Codex / Claude Code so skill discovery refreshes."
Write-Host "  2. Run: python scripts/validate_skills.py"
Write-Host "  3. Start production with: longform-engine production next project.yaml"
