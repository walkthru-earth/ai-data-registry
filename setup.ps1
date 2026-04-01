# ============================================================================
# Post-template setup script for ai-data-registry (Windows PowerShell)
# Run this after creating a new repo from the GitHub template.
# It replaces placeholder values and reinitializes the project for your use.
# ============================================================================

#Requires -Version 7.0
$ErrorActionPreference = 'Stop'

Write-Host "`nai-data-registry template setup`n" -ForegroundColor Cyan

# --- Check prerequisites ---------------------------------------------------

# 1. pixi (required)
if (-not (Get-Command pixi -ErrorAction SilentlyContinue)) {
    Write-Host 'pixi is not installed.' -ForegroundColor Red
    Write-Host ''
    Write-Host 'Install pixi first:'
    Write-Host '  winget install prefix-dev.pixi                              # Windows (winget)'
    Write-Host '  iwr -useb https://pixi.sh/install.ps1 | iex                # Windows (PowerShell)'
    Write-Host ''
    Write-Host 'Then re-run: .\setup.ps1'
    exit 1
}

Write-Host "  pixi found: $(pixi --version)" -ForegroundColor Green

# 2. Claude Code (recommended)
if (Get-Command claude -ErrorAction SilentlyContinue) {
    $claudeVer = try { claude --version 2>$null } catch { 'installed' }
    Write-Host "  Claude Code found: $claudeVer" -ForegroundColor Green
} else {
    Write-Host '  Claude Code not found (recommended).' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  Install Claude Code for AI-assisted development:'
    Write-Host '    irm https://claude.ai/install.ps1 | iex                   # PowerShell'
    Write-Host '    winget install Anthropic.ClaudeCode                        # WinGet'
    Write-Host ''
    Write-Host '  Note: Windows requires Git for Windows. Install it first if needed.'
    Write-Host '  Then start it in your project directory with: claude'
    Write-Host ''
}

Write-Host ''

# --- Gather info -----------------------------------------------------------

$ProjectName = Read-Host 'Project name (e.g. my-geo-project)'
if ([string]::IsNullOrWhiteSpace($ProjectName)) {
    Write-Host 'Project name is required.' -ForegroundColor Red
    exit 1
}

$DefaultAuthor = try { git config user.name } catch { 'Your Name' }
$AuthorName = Read-Host "Author name [$DefaultAuthor]"
if ([string]::IsNullOrWhiteSpace($AuthorName)) { $AuthorName = $DefaultAuthor }

$DefaultEmail = try { git config user.email } catch { 'you@example.com' }
$AuthorEmail = Read-Host "Author email [$DefaultEmail]"
if ([string]::IsNullOrWhiteSpace($AuthorEmail)) { $AuthorEmail = $DefaultEmail }

$Description = Read-Host 'Description (one line) [Geospatial data processing project]'
if ([string]::IsNullOrWhiteSpace($Description)) { $Description = 'Geospatial data processing project' }

$Version = Read-Host 'Version [0.1.0]'
if ([string]::IsNullOrWhiteSpace($Version)) { $Version = '0.1.0' }

Write-Host "`nApplying settings..." -ForegroundColor Yellow

# --- Replace placeholders in pixi.toml ------------------------------------

$pixiToml = Get-Content -Path 'pixi.toml' -Raw
$pixiToml = $pixiToml -replace 'name = "ai-data-registry"', "name = `"$ProjectName`""
$pixiToml = $pixiToml -replace 'authors = \[.*?\]', "authors = [`"$AuthorName <$AuthorEmail>`"]"
$pixiToml = $pixiToml -replace 'version = "0.1.0"', "version = `"$Version`""
Set-Content -Path 'pixi.toml' -Value $pixiToml -NoNewline

# --- Replace placeholders in CLAUDE.md ------------------------------------

$claudeMd = Get-Content -Path 'CLAUDE.md' -Raw
$claudeMd = $claudeMd -replace 'ai-data-registry', $ProjectName
Set-Content -Path 'CLAUDE.md' -Value $claudeMd -NoNewline

# --- Replace in .claude/ agent/skill files --------------------------------

Get-ChildItem -Path '.claude' -Filter '*.md' -Recurse | ForEach-Object {
    $content = Get-Content -Path $_.FullName -Raw
    if ($content -match 'ai-data-registry') {
        $content = $content -replace 'ai-data-registry', $ProjectName
        Set-Content -Path $_.FullName -Value $content -NoNewline
    }
}

# --- Replace placeholders in .env.example ----------------------------------

if (Test-Path '.env.example') {
    $envExample = Get-Content -Path '.env.example' -Raw
    if ($envExample -match 'ai-data-registry') {
        $envExample = $envExample -replace 'ai-data-registry', $ProjectName
        Set-Content -Path '.env.example' -Value $envExample -NoNewline
    }
}

# --- Clean up template-specific files --------------------------------------

Remove-Item -Path '.github/workflows/template-setup.yml' -ErrorAction SilentlyContinue

# --- Generate .env from .env.example ----------------------------------------

if ((Test-Path '.env.example') -and -not (Test-Path '.env')) {
    Write-Host ''
    $SetupEnv = Read-Host 'Set up local secrets (.env)? [y/N]'
    if ($SetupEnv -match '^[Yy]$') {
        Copy-Item '.env.example' '.env'
        Write-Host '  Created .env from .env.example' -ForegroundColor Green

        $S3Url = Read-Host '  S3 endpoint URL (e.g. https://fsn1.your-objectstorage.com)'
        if ($S3Url) { (Get-Content '.env' -Raw) -replace 'S3_ENDPOINT_URL=.*', "S3_ENDPOINT_URL=$S3Url" | Set-Content '.env' -NoNewline }

        $S3Bucket = Read-Host '  S3 bucket name'
        if ($S3Bucket) { (Get-Content '.env' -Raw) -replace 'S3_BUCKET=.*', "S3_BUCKET=$S3Bucket" | Set-Content '.env' -NoNewline }

        $S3Region = Read-Host '  S3 region'
        if ($S3Region) { (Get-Content '.env' -Raw) -replace 'S3_REGION=.*', "S3_REGION=$S3Region" | Set-Content '.env' -NoNewline }

        $S3Key = Read-Host '  S3 write key ID'
        if ($S3Key) { (Get-Content '.env' -Raw) -replace 'S3_WRITE_KEY_ID=.*', "S3_WRITE_KEY_ID=$S3Key" | Set-Content '.env' -NoNewline }

        $S3Secret = Read-Host '  S3 write secret'
        if ($S3Secret) { (Get-Content '.env' -Raw) -replace 'S3_WRITE_SECRET=.*', "S3_WRITE_SECRET=$S3Secret" | Set-Content '.env' -NoNewline }

        Write-Host ''
        Write-Host '  S3 secrets saved to .env' -ForegroundColor Green
        Write-Host '  For Hetzner/HuggingFace tokens, edit .env manually.' -ForegroundColor Yellow
        Write-Host '  For GitHub repo secrets (CI), see docs/secrets-setup.md' -ForegroundColor Yellow
    } else {
        Write-Host '  Skipped. Copy .env.example to .env later when ready.' -ForegroundColor Yellow
    }
}

# --- Install pixi environment ---------------------------------------------

Write-Host ''
Write-Host 'Running pixi install...' -ForegroundColor Yellow
pixi install

# --- Remove setup scripts (after everything succeeds) ----------------------

Remove-Item -Path 'setup.sh' -ErrorAction SilentlyContinue
Remove-Item -Path 'setup.ps1' -ErrorAction SilentlyContinue

# --- Done ------------------------------------------------------------------

Write-Host "`nDone! Project '$ProjectName' is ready.`n" -ForegroundColor Green
Write-Host 'Next steps:'
Write-Host "  1. Review pixi.toml and CLAUDE.md"
Write-Host '  2. Create your first workspace:  /new-workspace <name> <language>'
Write-Host "  3. Commit:  git add -A && git commit -m 'Initialize $ProjectName from template'"
Write-Host ''
