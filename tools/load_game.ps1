# Load / restore a game project (swap the 5 game JSON files in game_data/).
# NOTE: keep this file ASCII-only so Windows PowerShell 5.1 (GBK default) parses it.
#
# Usage (run from repo root):
#   .\tools\load_game.ps1                       # loads games/demo_minimal
#   .\tools\load_game.ps1 -Game demo_minimal    # explicit project name under games/
#   .\tools\load_game.ps1 -Restore              # restore from game_data.bak
#
# Notes:
#   - First load backs up current game_data to game_data.bak (never overwritten).
#   - Only the 5 game JSON files are replaced; npc_layouts.json is left untouched.
#   - Data is read into memory at app.py startup, so restart app.py after switching.

param(
    [string]$Game = "demo_minimal",
    [switch]$Restore
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dataDir = Join-Path $root "game_data"
$bakDir  = Join-Path $root "game_data.bak"
$files = @("game_config.json", "scenes.json", "items.json",
           "enemies.json", "npc_dialogues.json")

if ($Restore) {
    if (-not (Test-Path $bakDir)) {
        Write-Host "No backup found at game_data.bak, nothing to restore." -ForegroundColor Red
        exit 1
    }
    foreach ($f in $files) {
        Copy-Item (Join-Path $bakDir $f) (Join-Path $dataDir $f) -Force
    }
    Write-Host "Restored game data from game_data.bak. Please restart app.py." -ForegroundColor Green
    exit 0
}

$srcDir = Join-Path (Join-Path $root "games") $Game
if (-not (Test-Path $srcDir)) {
    Write-Host "Project folder not found: $srcDir" -ForegroundColor Red
    exit 1
}

# Back up once before the first load (do not overwrite, so restore always works).
if (-not (Test-Path $bakDir)) {
    New-Item -ItemType Directory -Path $bakDir | Out-Null
    foreach ($f in $files) {
        Copy-Item (Join-Path $dataDir $f) (Join-Path $bakDir $f) -Force
    }
    Write-Host "Backed up current game_data to game_data.bak" -ForegroundColor DarkGray
}

foreach ($f in $files) {
    $src = Join-Path $srcDir $f
    if (-not (Test-Path $src)) {
        Write-Host "Project is missing file: $f" -ForegroundColor Red
        exit 1
    }
    Copy-Item $src (Join-Path $dataDir $f) -Force
}
Write-Host "Loaded project '$Game' into game_data. Restart app.py, then open http://127.0.0.1:5000/static/editor.html" -ForegroundColor Green
Write-Host "Restore original: .\tools\load_game.ps1 -Restore" -ForegroundColor DarkGray
