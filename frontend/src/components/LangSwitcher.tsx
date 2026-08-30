import { useTranslation } from 'react-i18next';

// es/en language switch shown in the header. Reuses the .tab pill styling so it blends
// with the tab bar. Default language is es (set in i18n/index.ts).
export function LangSwitcher() {
  const { i18n, t } = useTranslation();
  const current = i18n.language.startsWith('en') ? 'en' : 'es';

  const langs: Array<'es' | 'en'> = ['es', 'en'];
  return (
    <div className="tabs" style={{ margin: 0 }} role="group" aria-label={t('lang.aria')}>
      {langs.map((lng) => (
        <button
          key={lng}
          className={'tab' + (current === lng ? ' active' : '')}
          onClick={() => void i18n.changeLanguage(lng)}
        >
          {t('lang.' + lng)}
        </button>
      ))}
    </div>
  );
}
