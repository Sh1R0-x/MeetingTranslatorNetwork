import React from 'react';
import { RecordingStatus, ToggleState } from '../types';
import { Settings } from 'lucide-react';

interface HeaderProps {
  timer: number;
  status: RecordingStatus;
  toggles: ToggleState;
  onAction: (action: 'start' | 'pause' | 'stop' | 'reset') => void;
}

const formatTime = (seconds: number) => {
  const h = Math.floor(seconds / 3600).toString().padStart(2, '0');
  const m = Math.floor((seconds % 3600) / 60).toString().padStart(2, '0');
  const s = (seconds % 60).toString().padStart(2, '0');
  return `${h}:${m}:${s}`;
};

const StatusBadge: React.FC<{ label: string; isOn: boolean }> = ({ label, isOn }) => (
  <div className="flex items-center px-3 py-1 rounded-full bg-surface-dark border border-border-dark">
    <span className="text-[10px] uppercase text-text-muted mr-2 font-semibold">{label}</span>
    <span className={`text-[10px] font-bold ${isOn ? 'text-primary' : 'text-gray-500'}`}>
      {isOn ? 'ON' : 'OFF'}
    </span>
  </div>
);

export const Header: React.FC<HeaderProps> = ({ timer, status, toggles, onAction }) => {
  return (
    <header className="border-b border-border-dark bg-background-dark px-4 py-3 flex items-center justify-between shrink-0 h-16">
      {/* Left: Timer & Actions */}
      <div className="flex items-center gap-6">
        {/* Timer */}
        <div className="flex items-center gap-2">
          <span className={`text-[10px] font-bold tracking-wider ${status === 'RECORDING' ? 'text-red-500 animate-pulse' : 'text-gray-600'}`}>
            REC
          </span>
          <span className="font-mono text-2xl font-medium tracking-wide text-white">
            {formatTime(timer)}
          </span>
        </div>
        
        <div className="h-8 w-px bg-border-dark mx-2"></div>

        {/* Action Buttons */}
        <div className="flex items-center gap-3">
          <button 
            onClick={() => onAction('start')}
            className="px-4 py-1.5 rounded border border-green-500 text-green-500 hover:bg-green-500/10 text-xs font-semibold uppercase transition-colors"
          >
            Démarrer
          </button>
          <button 
            onClick={() => onAction('pause')}
            className="px-4 py-1.5 rounded border border-orange-500 text-orange-500 hover:bg-orange-500/10 text-xs font-semibold uppercase transition-colors"
          >
            Pause
          </button>
          <button 
            onClick={() => onAction('stop')}
            className="px-4 py-1.5 rounded border border-red-500 text-red-500 hover:bg-red-500/10 text-xs font-semibold uppercase transition-colors"
          >
            Arrêter
          </button>
          <button 
            onClick={() => onAction('reset')}
            className="px-4 py-1.5 rounded border border-gray-400 text-gray-400 hover:bg-gray-400/10 text-xs font-semibold uppercase transition-colors"
          >
            Reset
          </button>
        </div>
      </div>

      {/* Right: Status Badges & Settings */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2 hidden lg:flex">
          <StatusBadge label="Audio" isOn={toggles.audio} />
          <StatusBadge label="Transcription" isOn={toggles.transcription} />
          <StatusBadge label="Traduction" isOn={toggles.translation} />
          <StatusBadge label="Résumé" isOn={toggles.summary} />
        </div>
        
        <button className="p-2 text-text-muted hover:text-white transition-colors rounded-full hover:bg-surface-dark">
          <Settings size={20} />
        </button>
      </div>
    </header>
  );
};