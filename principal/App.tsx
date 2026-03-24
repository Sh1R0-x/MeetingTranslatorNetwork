import React, { useState, useEffect } from 'react';
import { Header } from './components/Header';
import { Navigation } from './components/Navigation';
import { Footer } from './components/Footer';
import { LanguageToolbar } from './components/LanguageToolbar';
import { ChatArea } from './components/ChatArea';
import { AppTab, RecordingStatus, ChatMessage, ToggleState } from './types';

// Mock initial data
const INITIAL_MESSAGES: ChatMessage[] = [
  {
    id: '1',
    timestamp: '10:05',
    author: 'MOI',
    content: 'Welcome to the meeting. We will be discussing the quarterly projections and the new marketing strategy for Q3.'
  }
];

const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<AppTab>('LIVE');
  const [timer, setTimer] = useState(0);
  const [status, setStatus] = useState<RecordingStatus>('IDLE');
  const [toggles] = useState<ToggleState>({
    audio: false,
    transcription: false,
    translation: false,
    summary: false,
  });

  // Timer logic
  useEffect(() => {
    let interval: number;
    if (status === 'RECORDING') {
      interval = window.setInterval(() => {
        setTimer((prev) => prev + 1);
      }, 1000);
    }
    return () => clearInterval(interval);
  }, [status]);

  const handleAction = (action: 'start' | 'pause' | 'stop' | 'reset') => {
    switch (action) {
      case 'start':
        setStatus('RECORDING');
        break;
      case 'pause':
        setStatus('PAUSED');
        break;
      case 'stop':
        setStatus('IDLE');
        break;
      case 'reset':
        setStatus('IDLE');
        setTimer(0);
        break;
    }
  };

  return (
    <div className="bg-background-light dark:bg-background-dark text-slate-800 dark:text-white flex flex-col h-screen antialiased overflow-hidden font-display">
      
      <Header 
        timer={timer} 
        status={status} 
        toggles={toggles}
        onAction={handleAction} 
      />
      
      <Navigation 
        activeTab={activeTab} 
        onTabChange={setActiveTab} 
      />

      <main className="flex-1 flex flex-col p-4 gap-4 overflow-hidden relative">
        {activeTab === 'LIVE' ? (
          <>
            <LanguageToolbar />
            <ChatArea 
              messages={INITIAL_MESSAGES} 
              startTime="10:04:55" 
            />
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-text-muted">
            <div className="text-center">
              <span className="material-icons text-6xl opacity-20 mb-4">construction</span>
              <p>View {activeTab} not implemented in this demo.</p>
            </div>
          </div>
        )}
      </main>

      <Footer />
    </div>
  );
};

export default App;