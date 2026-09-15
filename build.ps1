# Construit BNP2OFX.exe (portable) et BNP2OFX-Setup.exe (installeur).
# Usage :  powershell -ExecutionPolicy Bypass -File .\build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "==> Dépendances Python"
python -m pip install -r requirements.txt
python -m pip install pillow pyinstaller

Write-Host "==> Icône"
python tools\make_icon.py

Write-Host "==> Exécutable portable (dist\BNP2OFX.exe)"
python -m PyInstaller --noconfirm --clean BNP2OFX.spec
if (-not (Test-Path "dist\BNP2OFX.exe")) {
    throw "BNP2OFX.exe n'a pas été généré"
}

$setupBuilt = $false
$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if (-not $iscc) {
    $guess = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
    if (Test-Path $guess) { $iscc = $guess }
}
if ($iscc) {
    Write-Host "==> Installeur Inno Setup (dist\BNP2OFX-Setup.exe)"
    & $iscc "installer\bnp2ofx.iss"
    $setupBuilt = Test-Path "dist\BNP2OFX-Setup.exe"
}

if (-not $setupBuilt) {
    Write-Host "==> Installeur de secours (sans Inno Setup)"
    $packed = Join-Path $env:TEMP "BNP2OFX-packed.exe"
    Copy-Item "dist\BNP2OFX.exe" $packed -Force
    $env:BNP2OFX_PACKED = $packed
    python -m PyInstaller --noconfirm BNP2OFX-Setup.spec
}

Write-Host ""
Write-Host "Fichiers prêts dans dist\ :"
Get-ChildItem dist\*.exe | ForEach-Object { Write-Host ("  " + $_.FullName + "  (" + [math]::Round($_.Length / 1MB, 1) + " Mo)") }
Write-Host ""
Write-Host "Pour un usage simple : donnez BNP2OFX-Setup.exe  (double-clic = installer)."
Write-Host "Variante sans installation : donnez BNP2OFX.exe  (double-clic = lancer)."
