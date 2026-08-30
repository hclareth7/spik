import type { ReactNode } from 'react';

// Big monospace score (.score-badge) with a muted suffix (e.g. "/100").
interface ScoreBadgeProps {
  value: ReactNode;
  suffix?: string;
}

export function ScoreBadge({ value, suffix }: ScoreBadgeProps) {
  return (
    <div className="score-badge">
      <span>{value}</span>
      {suffix !== undefined && <small>{suffix}</small>}
    </div>
  );
}
