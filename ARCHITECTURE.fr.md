# Architecture

Vue d'ensemble de la structure et de la conception de MeetingTranslatorNetwork.

[Read in English](ARCHITECTURE.md)

## Conception générale

MeetingTranslatorNetwork est une application desktop PyQt6 avec une architecture multi-couches :

```
┌─────────────────────────────────────────────┐
│            Couche UI (PyQt6)                │
│  main_window.py  │  setup_window.py         │
├─────────────────────────────────────────────┤
│            Couche Services                   │
│  recorder_service  │  postprocess_service    │
│  diarization_service │ meeting_summary_svc   │
├─────────────────────────────────────────────┤
│         Couche Threads / Workers             │
│  live_deepgram_thread │ live_assemblyai_thread│
│  postprocess_thread   │ postprocess_worker    │
├─────────────────────────────────────────────┤
│            Couche Audio                      │
│  wasapi_loopback (Windows)                   │
├─────────────────────────────────────────────┤
│       Configuration et Sécurité              │
│  secure_store (keyring / DPAPI)              │
└─────────────────────────────────────────────┘
```

## Structure des répertoires

```
src/
├── app.py                          # Point d'entrée, chargement des polices
├── main.py                         # Appelle app.main()
├── common.py                       # Constantes, journalisation debug
│
├── ui/
│   ├── main_window.py              # Fenêtre principale, workflow enregistrement
│   ├── setup_window.py             # Dialogue de configuration (5 onglets)
│   └── style.qss                   # Feuille de style thème sombre
│
├── audio/
│   └── wasapi_loopback.py          # Capture audio WASAPI Windows
│
├── services/
│   ├── recorder_service.py         # Enregistrement WAV multi-pistes
│   ├── postprocess_service.py      # Pipeline de post-traitement
│   ├── diarization_service.py      # Diarisation (pyannote + faster-whisper)
│   └── meeting_summary_service.py  # Résumé IA + export DOCX
│
├── threads/
│   ├── live_deepgram_thread.py     # Streaming WebSocket Deepgram
│   ├── live_assemblyai_thread.py   # Streaming AssemblyAI
│   └── postprocess_thread.py       # Lance le worker isolé en sous-processus
│
├── workers/
│   └── postprocess_worker.py       # Sous-processus autonome pour le traitement ML
│
└── config/
    └── secure_store.py             # Abstraction stockage sécurisé natif OS

assets/
├── fonts/                          # IBM Plex Sans, JetBrains Mono
└── branding/                       # Icônes, images installateur

scripts/
├── build_windows.ps1               # Script de build Windows
└── build_macos.sh                  # Script de build macOS

packaging/
└── windows/
    └── MeetingTranslatorNetwork.iss  # Configuration installateur Inno Setup
```

## Décisions de conception clés

### Post-traitement isolé

Le pipeline de post-traitement (diarisation, transcription, génération de rapport) s'exécute dans un **sous-processus Python séparé** (`postprocess_worker.py`). Cette isolation empêche les crashs causés par les bibliothèques ML natives (PyTorch, ONNX, faster-whisper) d'affecter le processus UI principal.

La communication entre le processus principal et le worker utilise du JSON via stdin/stdout.

### Enregistrement audio double piste

L'application enregistre deux fichiers WAV séparés :
- **Audio participants** : capturé via loopback WASAPI Windows (ou entrée audio virtuelle sur macOS)
- **Microphone utilisateur** : entrée micro directe

Cette séparation permet une diarisation déterministe et une transcription plus propre en post-traitement.

### Stockage sécurisé des identifiants

Les clés API sont stockées via le stockage sécurisé natif de l'OS :
1. **Primaire** : bibliothèque Python `keyring` (Windows Credential Manager, macOS Trousseau, Linux Secret Service)
2. **Fallback** (Windows uniquement) : chiffrement DPAPI si le backend keyring est indisponible

Aucun identifiant n'est stocké en clair dans les fichiers de configuration.

### Moteurs de transcription live

Deux moteurs sont supportés pour la transcription en direct :
- **Deepgram Nova-3** : optimisé pour le français avec un faible taux d'erreur
- **AssemblyAI** : alternative multilingue

Les deux utilisent du streaming WebSocket pour des résultats en temps réel. Le moteur est sélectionnable dans la configuration.

## Flux de données

### Session d'enregistrement

```
Démarrer l'enregistrement
    │
    ├── RecorderService → WAV (participants)
    ├── RecorderService → WAV (microphone)
    │
    ├── LiveDeepgramThread (ou AssemblyAI) → Transcription temps réel
    │   └── Optionnel : Traduction OpenAI (EN→FR)
    │
    └── UI : Affichage chat live avec labels des intervenants
```

### Post-traitement

```
Arrêt enregistrement → Sélection des options
    │
    PostprocessThread → lance PostprocessWorker (sous-processus)
        │
        ├── Diarisation (pyannote) → Segments par intervenant
        ├── Transcription (faster-whisper) → Texte complet
        ├── Fusion et alignement des segments
        │
        ├── Optionnel : Résumé IA Perplexity
        │
        └── Génération du rapport DOCX
```

## Configuration

Les paramètres de l'application sont stockés dans :
- **Windows** : `%LOCALAPPDATA%\MeetingTranslatorNetwork\config.json`
- **macOS** : `~/Library/Application Support/MeetingTranslatorNetwork/config.json`
- **Linux** : `~/.config/MeetingTranslatorNetwork/config.json`

Les sessions sont enregistrées dans :
- Par défaut : `~/MeetingTranslatorSessions/`
- Configurable via le dialogue de paramètres
