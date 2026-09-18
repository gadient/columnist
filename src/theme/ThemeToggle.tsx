// Theme toggle for the global nav. Cycles light → dark → system.
// Styled with inline styles (like the rest of the nav) but referencing CSS vars
// so it themes itself. The icon reflects the current *preference*: sun (light),
// moon (dark), monitor (system).
import React from 'react';
import { Sun, Moon, Monitor } from 'lucide-react';
import { useTheme } from './ThemeContext';

const LABEL: Record<string, string> = {
  light: 'Light theme — click for dark',
  dark: 'Dark theme — click for system',
  system: 'System theme — click for light',
};

export function ThemeToggle() {
  const { preference, cycle } = useTheme();
  const Icon = preference === 'light' ? Sun : preference === 'dark' ? Moon : Monitor;

  return (
    <button
      onClick={cycle}
      title={LABEL[preference]}
      aria-label={LABEL[preference]}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: '2rem',
        height: '2rem',
        backgroundColor: 'rgb(var(--surface-sunken))',
        color: 'rgb(var(--text-muted))',
        border: '1px solid rgb(var(--border-default))',
        borderRadius: '0.5rem',
        cursor: 'pointer',
        marginRight: '0.5rem',
      }}
    >
      <Icon size={16} />
    </button>
  );
}
