// Pill tab bar (.tabs / .tab / .tab.active). `hidden` ids are not rendered (used to
// hide capture tabs in server mode).
export interface TabDef {
  id: string;
  label: string;
}

interface TabsProps {
  tabs: TabDef[];
  active: string;
  onChange: (id: string) => void;
  hidden?: string[];
}

export function Tabs({ tabs, active, onChange, hidden = [] }: TabsProps) {
  return (
    <div className="tabs" role="tablist">
      {tabs
        .filter((tab) => !hidden.includes(tab.id))
        .map((tab) => (
          <button
            key={tab.id}
            className={'tab' + (active === tab.id ? ' active' : '')}
            role="tab"
            aria-selected={active === tab.id}
            onClick={() => onChange(tab.id)}
          >
            {tab.label}
          </button>
        ))}
    </div>
  );
}
