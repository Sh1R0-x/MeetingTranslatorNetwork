Param(
    [string]$Python = "venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"

Write-Host "Building MeetingTranslatorNetwork for Windows..."

if (!(Test-Path $Python)) {
    throw "Python introuvable: $Python"
}

& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements.txt
& $Python -m pip install pyinstaller

& $Python -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --name MeetingTranslatorNetwork `
  --paths src `
  --add-data "src\ui\style.qss;ui" `
  --add-data "assets;assets" `
  src\main.py

Write-Host "Build terminé: dist\MeetingTranslatorNetwork\MeetingTranslatorNetwork.exe"

if (Get-Command iscc.exe -ErrorAction SilentlyContinue) {
    Write-Host "Inno Setup détecté, génération de l'installateur..."
    iscc.exe packaging\windows\MeetingTranslatorNetwork.iss
    Write-Host "Installateur généré dans output Inno Setup."
} else {
    Write-Host "Inno Setup non détecté (iscc.exe). EXE standalone disponible dans dist."
}
