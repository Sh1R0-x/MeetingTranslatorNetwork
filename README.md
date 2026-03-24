# MeetingTranslatorNetwork

Desktop application for meeting recording, live transcription, and structured post-meeting report generation.

[Lire en français](README.fr.md)

## Status

- **Production-ready** (v2026.01)
- Desktop UI: PyQt6
- Live transcription: Deepgram or AssemblyAI
- Post-processing: faster-whisper + DOCX generation + optional Perplexity summary

## Features

- **Multi-track audio recording**
  - Participant audio (Windows loopback / macOS virtual input)
  - User microphone
- **Live transcription**
  - Configurable engine: Deepgram (Nova-3) or AssemblyAI
  - Optional live EN → FR translation (OpenAI)
- **Speaker identification**
  - Recommended mode: reliable speaker labels in post-processing report
  - Beta mode: live speaker labels (less reliable)
- **Isolated post-processing** in a separate Python subprocess (prevents UI crashes from native ML libraries)
- **DOCX report generation** with structured templates
- **Optional AI summary** via Perplexity API (multiple templates: professional, webinar, recruitment, etc.)
- **Session history** with re-open capability

## Prerequisites

- Windows 10/11 or macOS 13+
- Python 3.11+
- Properly configured audio devices:
  - **Windows**: Audio output (loopback) + Audio input (microphone)
  - **macOS**: Two inputs (Participant source + Microphone), ideally using BlackHole or Loopback for participant capture

### API Keys (configured in-app)

| Service | Purpose | Required |
|---------|---------|----------|
| Deepgram | Live transcription (if selected) | Conditional |
| AssemblyAI | Live transcription (if selected) | Conditional |
| OpenAI | Live EN→FR translation | Optional |
| HuggingFace | Speaker diarization (pyannote models) | Optional |
| Perplexity | AI-powered meeting summaries | Optional |

## Installation

```bash
git clone https://github.com/Sh1R0-x/MeetingTranslatorNetwork.git
cd MeetingTranslatorNetwork
python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
```

## Usage

### Launch

```bash
# Windows
venv\Scripts\python.exe src\main.py

# macOS / Linux
venv/bin/python src/main.py
```

### Initial Configuration

Open **Configuration** in the app:

1. **Audio**: Select participant output device and microphone input device
2. **API Keys**: Enter the API keys for your chosen services
3. **Transcription**: Select live engine and speaker identification mode

### Workflow

1. Start recording
2. Follow live transcription in the Live tab (or dedicated chat window)
3. Stop recording
4. Choose post-processing options
5. Report generation: transcription + structured summary
6. View results in the History tab or session folder

## Output

Sessions are saved to:
- **Windows**: `%USERPROFILE%\MeetingTranslatorSessions\`
- **macOS/Linux**: `~/MeetingTranslatorSessions/`

The output directory can be changed in configuration.

Each session contains:
- WAV files (participant + microphone tracks)
- Transcription files
- Processing logs
- DOCX report (if enabled)

## Building

### Windows (EXE + optional Inno Setup installer)

```powershell
.\scripts\build_windows.ps1
```

Output in `artifacts/windows/`:
- `dist/MeetingTranslatorNetwork/` — standalone executable
- `installer/` — Inno Setup installer (if Inno Setup is installed)

See [assets/branding/README_BRANDING.txt](assets/branding/README_BRANDING.txt) for custom icons and branding.

### macOS (.app bundle, optional DMG)

```bash
chmod +x scripts/build_macos.sh
./scripts/build_macos.sh
```

### CI/CD

GitHub Actions workflow: `.github/workflows/build-v1.yml`
- Triggers on `v*` tags or manual dispatch
- Builds Windows and macOS artifacts

## Security

- API keys are stored using OS-native secure storage (`keyring` / Credential Manager / Keychain)
- Windows fallback: DPAPI-encrypted storage when keyring backend is unavailable
- No plaintext credentials are stored in configuration files
- See [SECURITY.md](SECURITY.md) for more details

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for a detailed overview of the project structure.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full history of changes.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines.

## Known Limitations

- Live speaker identification is inherently less stable than post-processing diarization
- Cross-platform build is not supported (PyInstaller does not cross-compile); use the target OS or CI
- macOS loopback capture requires a virtual audio driver (BlackHole or similar)

## License

[MIT](LICENSE)
