# MeetingTranslatorNetwork

Application desktop Windows pour l'enregistrement de réunions, la transcription live, puis la génération d'un compte rendu structuré en post-traitement.

## État du projet
- Statut: production utilisable
- UI: PyQt6
- Live: Deepgram ou AssemblyAI
- Post-traitement: faster-whisper + génération DOCX + résumé Perplexity (optionnel)

## Fonctionnalités principales
- Enregistrement audio multi-pistes:
  - `Audio des participants` (loopback Windows)
  - `Mon audio` (micro)
- Transcription live:
  - moteur configurable (`deepgram` ou `assemblyai`)
  - option de traduction live EN -> FR
- Identification voix:
  - mode recommandé: fiable en compte rendu
  - mode live beta: labels speaker en direct (moins fiable)
- Post-traitement isolé dans un sous-processus Python:
  - évite les crashs UI liés aux libs natives ML
- Génération de compte rendu DOCX avec template
- Historique des sessions et réouverture depuis l'application

## Architecture (résumé)

- `src/app.py`:
  - point d'entrée application
  - chargement des polices locales
- `src/ui/main_window.py`:
  - fenêtre principale, workflow enregistrement/live/stop/post-process
- `src/ui/setup_window.py`:
  - configuration audio, API, options live/post-traitement
- `src/services/recorder_service.py`:
  - capture audio participants + micro
- `src/threads/live_deepgram_thread.py`:
  - transcription live Deepgram
- `src/threads/live_assemblyai_thread.py`:
  - transcription live AssemblyAI
- `src/threads/postprocess_thread.py`:
  - orchestration post-traitement (worker séparé)
- `src/workers/postprocess_worker.py`:
  - worker de traitement isolé (transcription/diarization/docx)
- `src/services/postprocess_service.py`:
  - pipeline post-réunion
- `src/services/meeting_summary_service.py`:
  - résumé structuré / DOCX

## Workflow

1. Démarrer l'enregistrement
2. Suivre la transcription live dans l'onglet Live (ou fenêtre chat dédiée)
3. Arrêter
4. Choisir les options de traitement
5. Génération transcription + compte rendu
6. Consulter dans l'onglet Historique ou dans le dossier session

## Prérequis
- Windows 10/11
- Python 3.11+
- Périphériques audio correctement configurés (sortie Windows + micro)

## Installation

```powershell
git clone https://github.com/Sh1R0-x/MeetingTranslatorNetwork.git
cd MeetingTranslatorNetwork
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Lancement

```powershell
venv\Scripts\python.exe src\main.py
```

## Configuration initiale

Dans `Configuration`:

- AUDIO:
  - sélectionner `Sortie audio` (participants)
  - sélectionner `Entrée audio` (micro)
- API:
  - `Deepgram API Key` (si moteur Deepgram)
  - `AssemblyAI API Key` (si moteur AssemblyAI)
  - `OpenAI API Key` (traduction live optionnelle)
  - `Perplexity API Key` (résumé optionnel)
  - `HuggingFace Token` (si diarization voix avancée en post-process)
- TRANSCRIPTION:
  - choisir le moteur live
  - choisir le mode d'identification de voix

## Dossiers et sorties

Les sessions sont écrites sous `recordings/`:

- WAV participants + micro
- fichiers de transcription
- fichiers de progression et logs de traitement
- DOCX final (si activé)

## Notes importantes

- La séparation des voix en live reste une fonctionnalité intrinsèquement moins stable que le post-traitement complet.
- Le mode recommandé pour des comptes rendus fiables: live lisible + identification voix en post-process.
- Le post-traitement est volontairement séparé en sous-processus pour robustesse.

## Patch notes

Le détail des évolutions livrées est dans:

- `PATCH_NOTES_FR.md`

## Licence

MIT
