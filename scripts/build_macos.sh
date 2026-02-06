#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "Building MeetingTranslatorNetwork for macOS..."

"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r requirements.txt
"$PYTHON_BIN" -m pip install pyinstaller

"$PYTHON_BIN" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name MeetingTranslatorNetwork \
  --paths src \
  --add-data "src/ui/style.qss:ui" \
  --add-data "assets:assets" \
  src/main.py

echo "App bundle generated: dist/MeetingTranslatorNetwork.app"

if command -v create-dmg >/dev/null 2>&1; then
  rm -f "dist/MeetingTranslatorNetwork.dmg"
  create-dmg \
    --volname "MeetingTranslatorNetwork" \
    --window-size 900 560 \
    --icon-size 100 \
    --app-drop-link 700 250 \
    "dist/MeetingTranslatorNetwork.dmg" \
    "dist/MeetingTranslatorNetwork.app"
  echo "DMG generated: dist/MeetingTranslatorNetwork.dmg"
else
  echo "create-dmg non installé: DMG non généré (app .app disponible)."
fi
