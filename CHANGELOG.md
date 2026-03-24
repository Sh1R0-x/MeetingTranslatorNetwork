# Changelog

All notable changes to MeetingTranslatorNetwork are documented in this file.

[Lire en français](CHANGELOG.fr.md)

## [2026.01] — 2026-02-06

Final release on `master` branch.

### Architecture & Stability
- Stabilized post-processing in a dedicated Python subprocess (`workers/postprocess_worker.py`) to isolate Whisper/Torch/ONNX from the UI
- Improved thread management (`postprocess_thread`) with cancellation support, explicit logs and error handling
- Better resilience during rapid Start/Stop and window close actions (prevents blocked UI states and thread errors)

### Audio Recording
- Improved recording service (`recorder_service`) for more stable participant/microphone audio streams
- More robust stream stop and clean resource release
- Maintained WAV track separation (participants + microphone) for deterministic report processing

### Live Transcription
- Integrated Deepgram engine (`threads/live_deepgram_thread.py`) with FR/EN-optimized configuration
- Maintained AssemblyAI engine (`threads/live_assemblyai_thread.py`) as alternative
- Adjusted streaming parameters (endpointing, segmentation, grouping) to reduce truncated phrases
- Preserved live EN→FR translation capability

### Speaker Identification
- Added configurable live identification mode (beta) with speaker labels
- Added recommended "reliable" mode: speaker identification primarily in the report
- Added live speaker rename (alias) in the UI
- Updated live messages when diarization information arrives retroactively

### UI (PyQt6)
- Redesigned and cleaned up the dark premium UI (`src/ui/style.qss`)
- Improved top bar: recording states, timer, REC indicator, action buttons
- Stabilized tabs: Live / Transcription / Summary / History
- Adjusted sizing, alignment, and interactions for extended use

### Live Chat UX
- Fixed live chat display bugs (empty bubbles, truncated text, unstable size recalculation)
- Added dedicated chat window for improved readability during long sessions
- Improved auto-scroll and manual scroll behavior
- Embedded and standardized typography via local assets

### Fonts
- Added local fonts: IBM Plex Sans, JetBrains Mono
- Application-level font loading via `src/app.py` (no system installation required)

### Configuration
- Reorganized configuration sections to clearly separate live transcription, report, and voice settings
- Added user-friendly options (less technical)
- Improved save/load for live options (engine, voice, translation, debug)

### Report / DOCX
- Improved report generation and DOCX export pipeline
- Better progress tracking and cancellation handling
- More robust integration of template, language, diarization, and participant extraction parameters

### Cost Indicators
- Updated live transcription cost indicators (OpenAI / Deepgram / AssemblyAI by engine)
- Formatted display in cents for better readability

### Cleanup
- Removed obsolete OpenAI live components
- Consolidated live logic around retained engines (Deepgram / AssemblyAI)
