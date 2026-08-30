// Labelled range slider (.slider — CSS in styles/global.css). Shows the current value on the
// right so the user sees the numeric effect. Used by the Speak Cam filter controls.
interface SliderProps {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
  format?: (value: number) => string;
  disabled?: boolean;
}

export function Slider({ label, value, min, max, step, onChange, format, disabled }: SliderProps) {
  return (
    <div className="setting-row" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 6 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <span className="setting-label">{label}</span>
        <span className="setting-value">{format ? format(value) : value}</span>
      </div>
      <input
        type="range"
        className="slider"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        aria-label={label}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </div>
  );
}
