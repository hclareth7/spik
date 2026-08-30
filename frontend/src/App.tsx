import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { Mode } from './api/types';
import { LangSwitcher } from './components/LangSwitcher';
import { Tabs, type TabDef } from './components/Tabs';
import { CheckerTab } from './features/checker/CheckerTab';
import { FeedbackTab } from './features/feedback/FeedbackTab';
import { RecordTab } from './features/record/RecordTab';
import { useMode } from './hooks/useMode';
import { AppProvider, useApp } from './state/AppContext';

// Inner shell: has access to the shared context. Owns the active-tab state and applies
// the mode gating from app.js applyMode():
//   local     — Record (default) + Feedback in the tab bar; Checker behind a settings gear.
//   appliance — same (the noise card inside Checker hides itself).
//   server    — Record hidden and no gear; Feedback is forced and shown by default.
function Shell({ mode }: { mode: Mode }) {
  const { t, i18n } = useTranslation();
  const { refreshProjects } = useApp();

  // Keep the document language + title in sync with the selected UI language (es/en) so
  // the <html lang> attribute and the browser tab reflect the switch (a11y + correctness).
  useEffect(() => {
    document.documentElement.lang = i18n.language.startsWith('en') ? 'en' : 'es';
    document.title = t('header.documentTitle');
  }, [i18n.language, t]);

  // Checker moved out of the tab bar into a settings gear (below). Record is the primary tab.
  const tabs: TabDef[] = [
    { id: 'record', label: t('tabs.record') },
    { id: 'feedback', label: t('tabs.feedback') },
  ];
  const hidden = mode === 'server' ? ['record'] : [];
  const [active, setActive] = useState<string>(mode === 'server' ? 'feedback' : 'record');

  // A filesystem path handed from Record → Feedback so "Analyze" reuses the whole Feedback
  // flow (SSE, poll, progress, history) instead of duplicating it.
  const [pendingAnalyze, setPendingAnalyze] = useState<string | null>(null);
  const showSettings = mode !== 'server'; // Checker (camera/mic setup) doesn't apply on server

  // Re-fetch projects when entering the Feedback tab (app.js reloads them on tab open).
  useEffect(() => {
    if (active === 'feedback') void refreshProjects();
  }, [active, refreshProjects]);

  return (
    <div className="page">
      <div
        className="page-header"
        style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16 }}
      >
        <div>
          <div className="brand-row">
            <img src="/favicon.svg" className="brand-logo" alt="" aria-hidden="true" />
            <h1>
              <span className="accent">{t('header.brand')}</span>
              {t('header.titleSuffix')}
            </h1>
          </div>
          <p className="subtitle">{t('header.subtitle')}</p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {showSettings && (
            <div className="tabs" style={{ margin: 0 }}>
              <button
                className={'tab' + (active === 'checker' ? ' active' : '')}
                onClick={() => setActive('checker')}
                aria-label={t('settings.aria')}
                title={t('tabs.checker')}
                aria-pressed={active === 'checker'}
              >
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  style={{ display: 'block' }}
                >
                  <circle cx="12" cy="12" r="3" />
                  <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
                </svg>
              </button>
            </div>
          )}
          <LangSwitcher />
        </div>
      </div>

      <Tabs tabs={tabs} active={active} onChange={setActive} hidden={hidden} />

      {/* Only the active tab is mounted: this releases the camera when leaving Checker
          (a /dev/videoN cannot be opened twice) and tears down live SSE streams. */}
      {active === 'checker' && <CheckerTab mode={mode} />}
      {active === 'record' && (
        <RecordTab
          mode={mode}
          onRequestAnalyze={(p) => {
            setPendingAnalyze(p);
            setActive('feedback');
          }}
        />
      )}
      {active === 'feedback' && (
        <FeedbackTab
          mode={mode}
          pendingAnalyze={pendingAnalyze}
          onPendingConsumed={() => setPendingAnalyze(null)}
        />
      )}
    </div>
  );
}

export default function App() {
  const { config } = useMode();
  if (!config) return null; // brief: waiting for GET /api/config
  return (
    <AppProvider mode={config.mode}>
      <Shell mode={config.mode} />
    </AppProvider>
  );
}
