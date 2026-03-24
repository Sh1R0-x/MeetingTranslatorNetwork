import React from 'react';
import { AppTab } from '../types';

interface NavigationProps {
  activeTab: AppTab;
  onTabChange: (tab: AppTab) => void;
}

const TABS: AppTab[] = ['LIVE', 'TRANSCRIPTION', 'RÉSUMÉ', 'HISTORIQUE'];

export const Navigation: React.FC<NavigationProps> = ({ activeTab, onTabChange }) => {
  return (
    <nav className="bg-surface-dark border-b border-border-dark px-4 flex items-center shrink-0">
      {TABS.map((tab) => (
        <button
          key={tab}
          onClick={() => onTabChange(tab)}
          className={`px-6 py-3 text-sm font-semibold transition-colors ${
            activeTab === tab
              ? 'text-primary border-b-2 border-primary'
              : 'text-text-muted hover:text-white hover:bg-white/5'
          }`}
        >
          {tab}
        </button>
      ))}
    </nav>
  );
};