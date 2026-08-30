import { useEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

// Generic modal, portaled to <body> for the SAME reason as Picker: every .widget-card sets
// `backdrop-filter`, creating a stacking context a z-indexed child can't escape. Rendering
// into <body> lifts the overlay above all cards. Closes on Escape and backdrop click; on
// open it focuses the first focusable field so keyboard users land inside the dialog.
interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
}

export function Modal({ open, onClose, title, children, footer }: ModalProps) {
  const cardRef = useRef<HTMLDivElement>(null);

  // Escape-to-close (listener only while open).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  // Focus the first focusable control (input/button) when the dialog opens.
  useEffect(() => {
    if (!open) return;
    const first = cardRef.current?.querySelector<HTMLElement>(
      'input, textarea, select, button',
    );
    first?.focus();
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        // Close only when the press starts on the backdrop itself, not inside the card.
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="modal-card"
        ref={cardRef}
        role="dialog"
        aria-modal="true"
      >
        <div className="modal-title">{title}</div>
        {children && <div className="modal-body">{children}</div>}
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}
