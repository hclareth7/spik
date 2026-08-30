import React from 'react';
import ReactDOM from 'react-dom/client';

// Vendored fonts (self-hosted via @fontsource — no external CDN / Google Fonts).
// Each import pulls the matching .woff2 out of node_modules and Vite bundles it into
// the build output, so the app makes NO external font request at runtime.
import '@fontsource/inter/300.css';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import '@fontsource/inter/700.css';
import '@fontsource/jetbrains-mono/300.css';
import '@fontsource/jetbrains-mono/400.css';
import '@fontsource/jetbrains-mono/500.css';

import './styles/tokens.css';
import './styles/global.css';
import './i18n';

import App from './App';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
