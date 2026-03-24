# Architecture

Overview of the MeetingTranslatorNetwork project structure and design.

[Lire en français](ARCHITECTURE.fr.md)

## High-Level Design

MeetingTranslatorNetwork is a PyQt6 desktop application with a multi-layered architecture:

```
┌─────────────────────────────────────────────┐
│              UI Layer (PyQt6)               │
│  main_window.py  │  setup_window.py         │
├─────────────────────────────────────────────┤
│            Service Layer                     │
│  recorder_service  │  postprocess_service    │
│  diarization_service │ meeting_summary_svc   │
├─────────────────────────────────────────────┤
│           Thread / Worker Layer              │
│  live_deepgram_thread │ live_assemblyai_thread│
│  postprocess_thread   │ postprocess_worker    │
├─────────────────────────────────────────────┤
│            Audio Layer                       │
│  wasapi_loopback (Windows)                   │
├─────────────────────────────────────────────┤
│         Configuration & Security             │
│  secure_store (keyring / DPAPI)              │
└─────────────────────────────────────────────┘
```

## Directory Structure

```
src/
├── app.py                          # Entry point, font loading
├── main.py                         # Calls app.main()
├── common.py                       # Constants, debug logging
│
├── ui/
│   ├── main_window.py              # Main window, recording workflow
│   ├── setup_window.py             # Configuration dialog (5 tabs)
│   └── style.qss                   # Dark theme stylesheet
│
├── audio/
│   └── wasapi_loopback.py          # Windows WASAPI audio capture
│
├── services/
│   ├── recorder_service.py         # Multi-track WAV recording
│   ├── postprocess_service.py      # Post-meeting processing pipeline
│   ├── diarization_service.py      # Speaker diarization (pyannote + faster-whisper)
│   └── meeting_summary_service.py  # AI summary + DOCX export
│
├── threads/
│   ├── live_deepgram_thread.py     # Deepgram WebSocket streaming
│   ├── live_assemblyai_thread.py   # AssemblyAI streaming
│   └── postprocess_thread.py       # Spawns isolated worker subprocess
│
├── workers/
│   └── postprocess_worker.py       # Standalone subprocess for ML processing
│
└── config/
    └── secure_store.py             # OS-native secret storage abstraction

assets/
├── fonts/                          # IBM Plex Sans, JetBrains Mono
└── branding/                       # Icons, installer images

scripts/
├── build_windows.ps1               # Windows build script
└── build_macos.sh                  # macOS build script

packaging/
└── windows/
    └── MeetingTranslatorNetwork.iss  # Inno Setup installer config
```

## Key Design Decisions

### Isolated Post-Processing

The post-processing pipeline (diarization, transcription, report generation) runs in a **separate Python subprocess** (`postprocess_worker.py`). This isolation prevents crashes caused by native ML libraries (PyTorch, ONNX, faster-whisper) from affecting the main UI process.

Communication between the main process and the worker uses JSON over stdin/stdout.

### Dual Audio Track Recording

The application records two separate WAV files:
- **Participant audio**: Captured via Windows WASAPI loopback (or virtual audio input on macOS)
- **User microphone**: Direct microphone input

This separation enables deterministic diarization and cleaner transcription during post-processing.

### Secure Credential Storage

API keys are stored using the OS-native secure storage:
1. **Primary**: Python `keyring` library (Windows Credential Manager, macOS Keychain, Linux Secret Service)
2. **Fallback** (Windows only): DPAPI encryption when keyring backend is unavailable

No credentials are stored in plaintext configuration files.

### Live Transcription Engines

Two engines are supported for live transcription:
- **Deepgram Nova-3**: Optimized for French with low word error rate
- **AssemblyAI**: Multilingual alternative

Both use WebSocket streaming for real-time results. The engine is selectable in configuration.

## Data Flow

### Recording Session

```
Start Recording
    │
    ├── RecorderService → WAV (participants)
    ├── RecorderService → WAV (microphone)
    │
    ├── LiveDeepgramThread (or AssemblyAI) → Real-time transcription
    │   └── Optional: OpenAI translation (EN→FR)
    │
    └── UI: Live chat display with speaker labels
```

### Post-Processing

```
Stop Recording → User selects options
    │
    PostprocessThread → spawns PostprocessWorker (subprocess)
        │
        ├── Diarization (pyannote) → Speaker segments
        ├── Transcription (faster-whisper) → Full text
        ├── Merge & align segments
        │
        ├── Optional: Perplexity AI summary
        │
        └── DOCX report generation
```

## Configuration

Application settings are stored in:
- **Windows**: `%LOCALAPPDATA%\MeetingTranslatorNetwork\config.json`
- **macOS**: `~/Library/Application Support/MeetingTranslatorNetwork/config.json`
- **Linux**: `~/.config/MeetingTranslatorNetwork/config.json`

Sessions output to:
- Default: `~/MeetingTranslatorSessions/`
- Configurable via the settings dialog
