import React from 'react';

export const Footer: React.FC = () => {
  return (
    <footer className="bg-surface-dark border-t border-border-dark px-4 py-2 flex items-center justify-between shrink-0 text-xs mt-auto h-10">
      <div className="flex items-center gap-6">
        <label className="flex items-center gap-2 cursor-pointer group">
          <div className="relative flex items-center">
            <input 
              type="checkbox" 
              defaultChecked 
              className="peer h-3.5 w-3.5 cursor-pointer appearance-none rounded-sm border border-gray-600 bg-background-dark checked:border-primary checked:bg-primary focus:outline-none focus:ring-1 focus:ring-primary/50 transition-all"
            />
            <span className="absolute text-white opacity-0 peer-checked:opacity-100 top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 pointer-events-none">
              <span className="material-icons text-[10px] leading-none">check</span>
            </span>
          </div>
          <span className="text-text-muted group-hover:text-white transition-colors">Auto-scroll</span>
        </label>
        
        <label className="flex items-center gap-2 cursor-pointer group">
          <div className="relative flex items-center">
            <input 
              type="checkbox" 
              defaultChecked 
              className="peer h-3.5 w-3.5 cursor-pointer appearance-none rounded-sm border border-gray-600 bg-background-dark checked:border-primary checked:bg-primary focus:outline-none focus:ring-1 focus:ring-primary/50 transition-all"
            />
            <span className="absolute text-white opacity-0 peer-checked:opacity-100 top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 pointer-events-none">
               <span className="material-icons text-[10px] leading-none">check</span>
            </span>
          </div>
          <span className="text-text-muted group-hover:text-white transition-colors">Live</span>
        </label>
      </div>

      <div className="flex items-center gap-6 font-mono">
        <div className="flex items-center gap-2">
          <span className="text-text-muted">Prix transcription:</span>
          <span className="text-gray-300">$0.02/min</span>
        </div>
        <div className="h-4 w-px bg-border-dark"></div>
        <div className="flex items-center gap-2">
          <span className="text-text-muted">Coût:</span>
          <span className="text-white font-bold">$0.00</span>
        </div>
      </div>
    </footer>
  );
};