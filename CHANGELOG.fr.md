# Journal des modifications

Toutes les modifications notables de MeetingTranslatorNetwork sont documentées dans ce fichier.

[Read in English](CHANGELOG.md)

## [2026.01] — 2026-02-06

Version finale sur la branche `master`.

### Architecture et robustesse
- Stabilisation du post-traitement en sous-processus Python dédié (`workers/postprocess_worker.py`) pour isoler Whisper/Torch/ONNX de l'UI
- Renforcement des threads de traitement (`postprocess_thread`) avec gestion d'annulation, logs et erreurs plus explicites
- Amélioration de la résilience lors des actions rapides Start/Stop et fermeture de fenêtre (évite les états UI bloqués et erreurs de thread)

### Enregistrement audio
- Amélioration du service d'enregistrement (`recorder_service`) pour une meilleure stabilité des flux audio participants/micro
- Gestion plus robuste des arrêts de stream et fermeture propre des ressources audio
- Maintien de la séparation des pistes WAV (participants + micro) pour un traitement déterministe en compte rendu

### Transcription live
- Intégration du moteur Deepgram (`threads/live_deepgram_thread.py`) avec configuration adaptée FR/EN
- Maintien du moteur AssemblyAI (`threads/live_assemblyai_thread.py`) comme alternative
- Paramètres de streaming ajustés (endpointing, segmentation, regroupement) pour limiter les phrases tronquées
- Conservation de la traduction live EN→FR selon configuration

### Identification des voix
- Mise en place d'un mode d'identification live configurable (beta) avec labels speaker
- Ajout d'un mode recommandé « fiable » : identification des voix principalement au compte rendu
- Ajout de renommage live des voix détectées (alias) dans l'UI
- Mise à jour des messages live quand des informations de diarisation arrivent après coup

### Interface (PyQt6)
- Refonte et nettoyage de l'UI dark premium (`src/ui/style.qss`)
- Amélioration de la barre haute : états d'enregistrement, timer, REC, boutons d'action
- Onglets Live / Transcription / Résumé / Historique stabilisés et rendus plus lisibles
- Ajustements de tailles, alignements et interactions pour usage prolongé

### Chat live UX
- Correction de bugs d'affichage du chat live (bulles vides, texte tronqué, recalcul de taille instable)
- Ajout d'une fenêtre chat dédiée pour améliorer la lisibilité en sessions longues
- Auto-scroll et comportement de scroll manuel améliorés
- Typographie embarquée et homogénéisée via assets locaux

### Polices
- Ajout des polices locales : IBM Plex Sans, JetBrains Mono
- Chargement applicatif des polices via `src/app.py` (pas d'installation système requise)

### Configuration
- Réorganisation des sections de configuration pour distinguer clairement transcription live, compte rendu et voix
- Ajout d'options explicites orientées utilisateur (moins techniques)
- Sauvegarde/chargement renforcés des options live (moteur, voix, traduction, debug)

### Compte rendu / DOCX
- Renforcement du flux de génération de compte rendu et export DOCX
- Gestion de progression et d'annulation améliorée
- Intégration plus robuste des paramètres de template, langue, diarisation et extraction participants

### Indicateurs de coûts
- Mise à jour des indicateurs de coût transcription live (OpenAI / Deepgram / AssemblyAI selon moteur)
- Affichage formaté en centimes pour meilleure lisibilité

### Nettoyage
- Suppression de composants live OpenAI obsolètes
- Consolidation de la logique live autour des moteurs retenus (Deepgram / AssemblyAI)
