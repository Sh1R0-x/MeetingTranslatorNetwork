# MeetingTranslatorNetwork

Application desktop pour l'enregistrement de réunions, la transcription en direct et la génération de comptes rendus structurés en post-traitement.

[Read in English](README.md)

## État du projet

- **Production utilisable** (v2026.01)
- Interface : PyQt6
- Transcription live : Deepgram ou AssemblyAI
- Post-traitement : faster-whisper + génération DOCX + résumé Perplexity (optionnel)

## Fonctionnalités

- **Enregistrement audio multi-pistes**
  - Audio des participants (loopback Windows / entrée virtuelle macOS)
  - Microphone utilisateur
- **Transcription en direct**
  - Moteur configurable : Deepgram (Nova-3) ou AssemblyAI
  - Traduction live EN → FR optionnelle (OpenAI)
- **Identification des voix**
  - Mode recommandé : labels fiables dans le compte rendu post-traitement
  - Mode beta : labels en direct (moins fiable)
- **Post-traitement isolé** dans un sous-processus Python dédié (évite les crashs UI liés aux bibliothèques ML natives)
- **Génération de compte rendu DOCX** avec templates structurés
- **Résumé IA optionnel** via API Perplexity (templates : professionnel, webinaire, recrutement, etc.)
- **Historique des sessions** avec réouverture

## Prérequis

- Windows 10/11 ou macOS 13+
- Python 3.11+
- Périphériques audio correctement configurés :
  - **Windows** : Sortie audio (loopback) + Entrée audio (micro)
  - **macOS** : Deux entrées (Source participants + Micro), idéalement avec BlackHole/Loopback pour la source participants

### Clés API (configurées dans l'application)

| Service | Usage | Requis |
|---------|-------|--------|
| Deepgram | Transcription live (si sélectionné) | Conditionnel |
| AssemblyAI | Transcription live (si sélectionné) | Conditionnel |
| OpenAI | Traduction live EN→FR | Optionnel |
| HuggingFace | Diarisation (modèles pyannote) | Optionnel |
| Perplexity | Résumés IA de réunion | Optionnel |

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

## Utilisation

### Lancement

```bash
# Windows
venv\Scripts\python.exe src\main.py

# macOS / Linux
venv/bin/python src/main.py
```

### Configuration initiale

Ouvrir **Configuration** dans l'application :

1. **Audio** : Sélectionner le périphérique de sortie participants et le micro
2. **Clés API** : Saisir les clés pour les services choisis
3. **Transcription** : Choisir le moteur live et le mode d'identification des voix

### Workflow

1. Démarrer l'enregistrement
2. Suivre la transcription live dans l'onglet Live (ou fenêtre chat dédiée)
3. Arrêter l'enregistrement
4. Choisir les options de post-traitement
5. Génération du compte rendu : transcription + résumé structuré
6. Consulter les résultats dans l'onglet Historique ou le dossier de session

## Sorties

Les sessions sont enregistrées dans :
- **Windows** : `%USERPROFILE%\MeetingTranslatorSessions\`
- **macOS/Linux** : `~/MeetingTranslatorSessions/`

Le dossier de sortie est configurable.

Contenu de chaque session :
- Fichiers WAV (pistes participants + micro)
- Fichiers de transcription
- Logs de traitement
- Compte rendu DOCX (si activé)

## Build

### Windows (EXE + installateur Inno Setup optionnel)

```powershell
.\scripts\build_windows.ps1
```

Sorties dans `artifacts/windows/` :
- `dist/MeetingTranslatorNetwork/` — exécutable standalone
- `installer/` — installateur Inno Setup (si installé)

Voir [assets/branding/README_BRANDING.txt](assets/branding/README_BRANDING.txt) pour le branding personnalisé.

### macOS (bundle .app, DMG optionnel)

```bash
chmod +x scripts/build_macos.sh
./scripts/build_macos.sh
```

### CI/CD

Workflow GitHub Actions : `.github/workflows/build-v1.yml`
- Déclenchement sur tags `v*` ou dispatch manuel
- Build Windows et macOS

## Sécurité

- Les clés API sont stockées via le stockage sécurisé natif de l'OS (`keyring` / Credential Manager / Trousseau)
- Fallback Windows : stockage chiffré DPAPI si le backend keyring est indisponible
- Aucun identifiant en clair dans les fichiers de configuration
- Voir [SECURITY.md](SECURITY.md) pour plus de détails

## Architecture

Voir [ARCHITECTURE.fr.md](ARCHITECTURE.fr.md) pour une vue détaillée de la structure du projet.

## Changelog

Voir [CHANGELOG.fr.md](CHANGELOG.fr.md) pour l'historique complet des modifications.

## Contribuer

Voir [CONTRIBUTING.md](CONTRIBUTING.md) pour les directives de contribution.

## Limitations connues

- L'identification des voix en direct est intrinsèquement moins stable que la diarisation en post-traitement
- Le build cross-platform n'est pas supporté (PyInstaller ne cross-compile pas) ; utiliser l'OS cible ou la CI
- La capture loopback macOS nécessite un pilote audio virtuel (BlackHole ou similaire)

## Licence

[MIT](LICENSE)
