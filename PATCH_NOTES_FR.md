# MeetingTranslatorNetwork - Patch Notes (FR)

Version: finale (branche `master`)
Date: 2026-02-06

## 1) Architecture et robustesse
- Stabilisation du post-traitement en sous-processus Python dédié (`workers/postprocess_worker.py`) pour isoler Whisper/Torch/ONNX de l'UI.
- Renforcement des threads de traitement (`postprocess_thread`) avec gestion d'annulation, logs et erreurs plus explicites.
- Amélioration de la résilience lors des actions rapides Start/Stop et fermeture de fenêtre (évite les états UI bloqués et erreurs de thread).

## 2) Enregistrement audio
- Amélioration du service d'enregistrement (`recorder_service`) pour une meilleure stabilité des flux audio participants/micro.
- Gestion plus robuste des arrêts de stream et fermeture propre des ressources audio.
- Maintien de la séparation des pistes WAV (participants + micro) pour un traitement déterministe en compte rendu.

## 3) Transcription live: moteurs et qualité
- Intégration du moteur live Deepgram (`threads/live_deepgram_thread.py`) avec configuration adaptée FR/EN.
- Intégration/maintien du moteur AssemblyAI (`threads/live_assemblyai_thread.py`) comme alternative.
- Paramètres de streaming ajustés (endpointing, segmentation, regroupement) pour limiter les phrases tronquées.
- Gestion de la traduction live EN->FR conservée selon configuration.

## 4) Identification des voix
- Mise en place d'un mode d'identification live configurable (beta) avec labels speaker.
- Ajout d'un mode recommandé "fiable": identification des voix principalement au compte rendu.
- Ajout de renommage live des voix détectées (alias) dans l'UI.
- Mise à jour des messages live quand des informations de diarization arrivent après coup.

## 5) UI principale (PyQt6)
- Refonte et nettoyage de l'UI dark premium (`src/ui/style.qss`).
- Amélioration de la barre haute: états d'enregistrement, timer, REC, boutons d'action.
- Onglets Live / Transcription / Résumé / Historique stabilisés et rendus plus lisibles.
- Ajustements de tailles, alignements et interactions pour usage prolongé.

## 6) Live Chat UX
- Stabilisation de l'affichage du chat live (suppression de bugs de bulles vides, texte tronqué, recalcul de taille instable).
- Ajout d'une fenêtre chat dédiée pour améliorer la lisibilité en session longue.
- Auto-scroll et comportement de scroll manuel améliorés.
- Typographie embarquée et homogénéisée via assets locaux.

## 7) Polices intégrées au projet
- Ajout des polices locales:
  - `assets/fonts/IBMPlexSans.ttf`
  - `assets/fonts/JetBrainsMono.ttf`
- Chargement applicatif des polices via `src/app.py` pour éviter une installation système.

## 8) Configuration (Setup)
- Réorganisation des sections de configuration pour distinguer clairement transcription live, compte rendu et voix.
- Ajout d'options explicites orientées utilisateur (moins techniques).
- Sauvegarde/chargement renforcés des options live (moteur, voix, traduction, debug).

## 9) Compte rendu / DOCX
- Renforcement du flux de génération de compte rendu et export DOCX.
- Gestion de progression et d'annulation améliorée.
- Intégration plus robuste des paramètres de template, langue, diarization et extraction participants.

## 10) Coûts et indicateurs
- Mise à jour des indicateurs de coût transcription live (OpenAI / Deepgram / AssemblyAI selon moteur).
- Affichage formaté en centimes pour meilleure lisibilité.

## 11) Nettoyage fonctionnel
- Suppression de composants live OpenAI obsolètes:
  - `src/services/live_openai_realtime_transcribe.py`
  - `src/services/live_translate_service.py`
  - `src/threads/live_openai_thread.py`
- Consolidation de la logique live autour des moteurs retenus (Deepgram / AssemblyAI).

## 12) Fichiers clés impactés
- `src/app.py`
- `src/services/recorder_service.py`
- `src/services/postprocess_service.py`
- `src/services/meeting_summary_service.py`
- `src/services/diarization_service.py`
- `src/threads/live_deepgram_thread.py`
- `src/threads/live_assemblyai_thread.py`
- `src/threads/postprocess_thread.py`
- `src/workers/postprocess_worker.py`
- `src/ui/main_window.py`
- `src/ui/setup_window.py`
- `src/ui/style.qss`
- `requirements.txt`

## Notes
- Les fichiers de tests/debug temporaires locaux ne font pas partie de cette version finale.
- La voie recommandée pour des speakers fiables reste: live lisible + validation/affinage en compte rendu.
