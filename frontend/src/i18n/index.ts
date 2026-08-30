import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import en from './en.json';
import es from './es.json';

// Default language is Spanish (the app's original UI language). The LangSwitcher in the
// header toggles es/en. No language detection / no external calls — all strings ship in
// the bundle. `escapeValue: false` is safe: we only interpolate our own values, and the
// few strings that carry <b> markup are rendered explicitly (never with user input).
void i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    es: { translation: es },
  },
  lng: 'es',
  fallbackLng: 'es',
  interpolation: { escapeValue: false },
});

export default i18n;
