// Pill toggle (.toggle / .toggle.active). Rendered as a button for accessibility.
interface ToggleProps {
  active: boolean;
  onClick: () => void;
  id?: string;
  'aria-label'?: string;
}

export function Toggle({ active, onClick, id, ...rest }: ToggleProps) {
  return (
    <button
      type="button"
      className={'toggle' + (active ? ' active' : '')}
      id={id}
      aria-label={rest['aria-label']}
      aria-pressed={active}
      onClick={onClick}
    />
  );
}
