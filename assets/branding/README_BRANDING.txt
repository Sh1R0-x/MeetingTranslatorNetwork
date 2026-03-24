MeetingTranslatorNetwork - Branding assets

Put your custom branding files in this folder.
All files are optional. If a file is missing, default app visuals are used.

Runtime app icon (PyQt):
- assets/branding/app_icon.png
  - Format: PNG
  - Recommended: 512x512 (square), transparent background
- or assets/branding/app_icon.ico
  - Format: ICO (multi-size recommended: 16/32/48/64/128/256)

Windows build and setup branding:
- assets/branding/windows/app.ico
  - Used by PyInstaller (--icon) for MeetingTranslatorNetwork.exe
  - Format: ICO, include at least 16/32/48/256

- assets/branding/windows/setup.ico
  - Used by Inno Setup for installer icon
  - Format: ICO, include at least 16/32/48/256

- assets/branding/windows/wizard.bmp
  - Used by Inno Setup as left-side wizard image
  - Format: BMP
  - Required size: 164x314

- assets/branding/windows/wizard_small.bmp
  - Used by Inno Setup as small top-right wizard image
  - Format: BMP
  - Required size: 55x55

Quick export tips:
- Keep source in SVG/Figma, export PNG first, then convert to ICO/BMP.
- For ICO generation, include multiple embedded sizes to avoid blurry icons.
