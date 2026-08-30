// Clean bulleted list (.list-clean) with colored dots (.dot.ok/.warn/.accent).
// Mirrors the li() helper in app.js. Text is rendered as a React child (auto-escaped).
export type DotClass = 'ok' | 'warn' | 'accent';

interface DotListProps {
  items: string[];
  dot: DotClass;
  id?: string;
}

export function DotList({ items, dot, id }: DotListProps) {
  return (
    <ul className="list-clean" id={id}>
      {items.map((text, i) => (
        <li key={i}>
          <span className={'dot ' + dot} />
          <span>{text}</span>
        </li>
      ))}
    </ul>
  );
}
