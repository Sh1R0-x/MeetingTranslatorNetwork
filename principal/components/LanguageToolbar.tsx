import React from 'react';
import { ArrowRight, MessageSquare, Edit2, ChevronDown } from 'lucide-react';

export const LanguageToolbar: React.FC = () => {
  return (
    <div className="flex flex-wrap items-center justify-between bg-surface-dark p-3 rounded-lg border border-border-dark shrink-0 gap-4">
      <div className="flex items-center gap-4 flex-wrap">
        {/* Dropdown 1 */}
        <div className="flex flex-col gap-1">
          <label className="text-[10px] uppercase text-text-muted font-bold tracking-wider">
            Langue du participant
          </label>
          <div className="relative">
            <select className="appearance-none bg-background-dark text-white text-sm pl-3 pr-8 py-1.5 rounded border border-border-dark focus:border-primary focus:ring-1 focus:ring-primary w-48 cursor-pointer outline-none">
              <option>Anglais (US)</option>
              <option>Français (FR)</option>
              <option>Espagnol (ES)</option>
            </select>
            <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center px-2 text-text-muted">
              <ChevronDown size={14} />
            </div>
          </div>
        </div>

        {/* Arrow Icon */}
        <ArrowRight size={16} className="text-text-muted mt-5" />

        {/* Dropdown 2 */}
        <div className="flex flex-col gap-1">
          <label className="text-[10px] uppercase text-text-muted font-bold tracking-wider">
            Ma langue source
          </label>
          <div className="relative">
            <select className="appearance-none bg-background-dark text-white text-sm pl-3 pr-8 py-1.5 rounded border border-border-dark focus:border-primary focus:ring-1 focus:ring-primary w-48 cursor-pointer outline-none">
              <option>Français (FR)</option>
              <option>Anglais (US)</option>
              <option>Allemand (DE)</option>
            </select>
            <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center px-2 text-text-muted">
              <ChevronDown size={14} />
            </div>
          </div>
        </div>
      </div>

      {/* Right Buttons */}
      <div className="flex items-center gap-3 pt-2 sm:pt-4">
        <button className="flex items-center gap-2 px-3 py-1.5 bg-primary/10 text-primary hover:bg-primary/20 rounded border border-primary/30 text-xs font-medium transition-colors">
          <MessageSquare size={14} />
          Ouvrir chat
        </button>
        <button className="flex items-center gap-2 px-3 py-1.5 bg-surface-dark text-text-muted hover:text-white hover:bg-white/5 rounded border border-border-dark text-xs font-medium transition-colors">
          <Edit2 size={14} />
          Renommer voix
        </button>
      </div>
    </div>
  );
};