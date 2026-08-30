import type { CSSProperties, ReactNode } from 'react';

// Glass panel (.widget-card). `wide` spans the full grid row (.widget-card.wide).
// `label` renders the uppercase .card-label; omit it when the header is custom.
interface WidgetCardProps {
  label?: ReactNode;
  wide?: boolean;
  children: ReactNode;
  id?: string;
  style?: CSSProperties;
}

export function WidgetCard({ label, wide, children, id, style }: WidgetCardProps) {
  return (
    <div className={'widget-card' + (wide ? ' wide' : '')} id={id} style={style}>
      {label !== undefined && <div className="card-label">{label}</div>}
      {children}
    </div>
  );
}
