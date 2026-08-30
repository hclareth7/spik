import type { ButtonHTMLAttributes } from 'react';

// Pill button (.glass). Variant maps to .primary / .danger; `sm` adds the compact size.
interface GlassButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'default' | 'primary' | 'danger';
  sm?: boolean;
}

export function GlassButton({
  variant = 'default',
  sm = false,
  className,
  children,
  ...rest
}: GlassButtonProps) {
  const cls = [
    'glass',
    variant === 'primary' ? 'primary' : '',
    variant === 'danger' ? 'danger' : '',
    sm ? 'sm' : '',
    className ?? '',
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <button className={cls} {...rest}>
      {children}
    </button>
  );
}
