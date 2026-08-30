// Analysis progress bar (.progress-wrap). The fill uses the accent->#22d3ee gradient
// defined in global.css. `pct` is clamped/rounded by the caller.
interface ProgressBarProps {
  stageLabel: string;
  pct: number;
}

export function ProgressBar({ stageLabel, pct }: ProgressBarProps) {
  const p = Math.max(0, Math.min(100, Math.round(pct || 0)));
  return (
    <div className="progress-wrap" style={{ marginTop: 14 }}>
      <div className="progress-head">
        <span>{stageLabel}</span>
        <span>{p}%</span>
      </div>
      <div className="progress-track">
        <div className="progress-fill" style={{ width: `${p}%` }} />
      </div>
    </div>
  );
}
