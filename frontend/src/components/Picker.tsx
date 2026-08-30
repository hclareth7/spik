import {
  Children,
  isValidElement,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';

// Custom dropdown that keeps the SAME API as a styled <select> (value / onChange / <option>
// children / style / id / aria-label), so every call site is unchanged. Native <option>
// menus can't be styled consistently across browsers; this renders its own listbox so the
// options match the app's dark glass design. The trigger is min-width:0 + ellipsis, so long
// values (e.g. PipeWire source names) never overflow their card.
//
// The listbox is portaled to <body> with position:fixed anchored to the trigger's rect.
// Reason: every .widget-card sets `backdrop-filter`, which creates a NEW stacking context —
// so a z-indexed dropdown rendered inside a card can never paint above a LATER sibling card.
// Escaping to <body> lifts the menu out of all card stacking contexts (Issue: "los select
// options salen debajo de otros componentes").
interface PickerProps {
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
  id?: string;
  style?: CSSProperties;
  'aria-label'?: string;
}

interface Opt {
  value: string;
  label: ReactNode;
}

interface Rect {
  top: number;
  left: number;
  width: number;
}

export function Picker({ value, onChange, children, id, style, ...rest }: PickerProps) {
  // Parse <option> children into {value, label} pairs (the only children callers pass).
  const options = useMemo<Opt[]>(() => {
    const out: Opt[] = [];
    Children.forEach(children, (child) => {
      if (isValidElement(child) && child.type === 'option') {
        const props = child.props as { value?: string; children?: ReactNode };
        out.push({ value: String(props.value ?? ''), label: props.children });
      }
    });
    return out;
  }, [children]);

  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const [rect, setRect] = useState<Rect | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const selected = options.find((o) => o.value === value);
  const selectedIdx = options.findIndex((o) => o.value === value);

  // Anchor the portaled listbox to the trigger's current viewport rect (position:fixed).
  const reposition = useCallback(() => {
    const el = rootRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setRect({ top: r.bottom + 6, left: r.left, width: r.width });
  }, []);

  // Keep the menu glued to the trigger while open (scroll/resize move the trigger).
  useLayoutEffect(() => {
    if (!open) return;
    reposition();
    window.addEventListener('scroll', reposition, true); // capture: catch scrolls in any ancestor
    window.addEventListener('resize', reposition);
    return () => {
      window.removeEventListener('scroll', reposition, true);
      window.removeEventListener('resize', reposition);
    };
  }, [open, reposition]);

  // Close on any click outside BOTH the trigger and the portaled list.
  useEffect(() => {
    if (!open) return;
    const onDocDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (rootRef.current?.contains(target)) return;
      if (listRef.current?.contains(target)) return;
      setOpen(false);
    };
    document.addEventListener('mousedown', onDocDown);
    return () => document.removeEventListener('mousedown', onDocDown);
  }, [open]);

  // When opening, highlight the current selection.
  const openMenu = () => {
    setHighlight(selectedIdx >= 0 ? selectedIdx : 0);
    setOpen(true);
  };

  const pick = (idx: number) => {
    const opt = options[idx];
    if (opt) onChange(opt.value);
    setOpen(false);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      setOpen(false);
      return;
    }
    if (!open) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openMenu();
      }
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setHighlight((h) => Math.min(h + 1, options.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      pick(highlight);
    }
  };

  return (
    <div className="picker2" ref={rootRef} style={style}>
      <button
        type="button"
        id={id}
        className="picker2-trigger"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={rest['aria-label']}
        onClick={() => (open ? setOpen(false) : openMenu())}
        onKeyDown={onKeyDown}
      >
        <span className="picker2-value">{selected ? selected.label : ''}</span>
        <svg
          className="picker2-chevron"
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
      {open &&
        rect &&
        createPortal(
          <ul
            ref={listRef}
            className="picker2-list"
            role="listbox"
            style={{ top: rect.top, left: rect.left, minWidth: rect.width }}
          >
            {options.map((o, i) => (
              <li
                key={o.value + String(i)}
                role="option"
                aria-selected={o.value === value}
                className={
                  'picker2-option' +
                  (o.value === value ? ' selected' : '') +
                  (i === highlight ? ' highlight' : '')
                }
                onMouseEnter={() => setHighlight(i)}
                onMouseDown={(e) => {
                  // mousedown (not click) so it fires before the document mousedown-close.
                  e.preventDefault();
                  pick(i);
                }}
              >
                {o.label}
              </li>
            ))}
          </ul>,
          document.body,
        )}
    </div>
  );
}
