// App-level error banner. A single dismissible bar the app surfaces when a
// backend call fails, so failures don't render as silent empty screens. Fixed to the top,
// above the nav, with an optional Retry action. Renders nothing when there's no error.
import React from 'react';
import { AlertTriangle, RefreshCw, X } from 'lucide-react';

export interface AppError {
  message: string;
  onRetry?: () => void;
}

export const ErrorBanner = ({ error, onDismiss }: { error: AppError | null; onDismiss: () => void }) => {
  if (!error) return null;
  return (
    <div
      role="alert"
      style={{
        position: 'fixed', top: 0, left: 0, right: 0, zIndex: 10000,
        display: 'flex', alignItems: 'center', gap: '0.625rem',
        padding: '0.625rem 1rem', backgroundColor: '#b91c1c', color: '#fff',
        fontSize: '0.8125rem', fontWeight: 500, boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
      }}
    >
      <AlertTriangle size={16} style={{ flexShrink: 0 }} />
      <span style={{ flex: 1 }}>{error.message}</span>
      {error.onRetry && (
        <button
          onClick={error.onRetry}
          style={{
            display: 'flex', alignItems: 'center', gap: '0.3rem', padding: '0.25rem 0.625rem',
            backgroundColor: 'rgba(255,255,255,0.15)', color: '#fff', border: '1px solid rgba(255,255,255,0.35)',
            borderRadius: '0.375rem', fontSize: '0.75rem', fontWeight: 600, cursor: 'pointer', whiteSpace: 'nowrap',
          }}
        >
          <RefreshCw size={13} /> Retry
        </button>
      )}
      <button onClick={onDismiss} aria-label="Dismiss" style={{ background: 'none', border: 'none', color: '#fff', cursor: 'pointer', opacity: 0.85, display: 'flex' }}>
        <X size={16} />
      </button>
    </div>
  );
};
