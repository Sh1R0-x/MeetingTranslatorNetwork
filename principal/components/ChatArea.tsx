import React from 'react';
import { ChatMessage } from '../types';

interface ChatAreaProps {
  messages: ChatMessage[];
  startTime: string;
}

export const ChatArea: React.FC<ChatAreaProps> = ({ messages, startTime }) => {
  return (
    <div className="flex-1 bg-surface-dark rounded-lg border border-border-dark relative overflow-hidden flex flex-col mt-4">
      {/* Chat Header */}
      <div className="px-4 py-2 border-b border-border-dark bg-surface-dark/50 flex justify-between items-center sticky top-0 backdrop-blur-sm z-10">
        <span className="text-xs font-semibold text-text-muted uppercase tracking-widest">
          Chat Live Preview
        </span>
        <span className="material-icons text-text-muted text-sm">history</span>
      </div>

      {/* Messages Scroll Area */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        
        {/* System Start Message */}
        <div className="flex justify-center mb-6">
          <span className="text-xs text-text-muted bg-background-dark px-3 py-1 rounded-full border border-border-dark">
            Séance démarrée à {startTime}
          </span>
        </div>

        {/* Message List */}
        {messages.map((msg) => (
          <div key={msg.id} className="flex flex-col gap-1 group">
            <div className="flex items-baseline gap-3">
              <span className="font-mono text-xs text-text-muted">[{msg.timestamp}]</span>
              <span className="font-bold text-sm text-primary uppercase">{msg.author}:</span>
            </div>
            <div className="ml-[4.5rem] bg-background-dark p-3 rounded-lg rounded-tl-none border border-border-dark max-w-3xl shadow-sm hover:border-primary/50 transition-colors">
              <p className="text-base text-gray-200 leading-relaxed">
                {msg.content}
              </p>
            </div>
          </div>
        ))}

        {/* Listening Indicator */}
        <div className="flex items-center gap-2 mt-4 ml-6 animate-pulse">
           <div className="w-1.5 h-1.5 rounded-full bg-primary"></div>
           <span className="text-xs text-primary font-medium">Listening...</span>
        </div>
      </div>

      {/* Bottom Gradient Fade */}
      <div className="absolute bottom-0 left-0 right-0 h-12 bg-gradient-to-t from-surface-dark to-transparent pointer-events-none"></div>
    </div>
  );
};