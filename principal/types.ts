export interface ChatMessage {
  id: string;
  timestamp: string;
  author: string;
  content: string;
  isSystem?: boolean;
}

export type AppTab = 'LIVE' | 'TRANSCRIPTION' | 'RÉSUMÉ' | 'HISTORIQUE';

export type RecordingStatus = 'IDLE' | 'RECORDING' | 'PAUSED';

export interface LanguageOption {
  code: string;
  label: string;
}

export interface ToggleState {
  audio: boolean;
  transcription: boolean;
  translation: boolean;
  summary: boolean;
}