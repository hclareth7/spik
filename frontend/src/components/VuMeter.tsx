// Live level meter (.vu-row). `fillPct` is 0..100; `fillColor` is a CSS color (a
// var(--…) token from colorForDb). `value` is the right-aligned dBFS readout.
interface VuMeterProps {
  label: string;
  fillPct: number;
  fillColor: string;
  value: string;
}

export function VuMeter({ label, fillPct, fillColor, value }: VuMeterProps) {
  return (
    <div className="vu-row" style={{ marginTop: 8 }}>
      <span className="vu-label">{label}</span>
      <div className="vu-track">
        <div className="vu-fill" style={{ width: `${fillPct}%`, background: fillColor }} />
      </div>
      <span className="vu-value">{value}</span>
    </div>
  );
}
