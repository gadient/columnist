import React from 'react';
import { X } from 'lucide-react';

export const DateField = ({ value, onChange }: { value: string; onChange: (v: string) => void }) => (
  <div className="flex gap-2">
    <input
      type="date"
      value={value || ''}
      onChange={e => onChange(e.target.value)}
      className="flex-1 px-3 py-2 bg-surface-raised border-2 border-line-strong rounded-lg text-sm text-fg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
    />
    {value && (
      <button
        onClick={() => onChange('')}
        className="px-3 py-2 bg-surface-raised text-red-600 dark:text-red-400 rounded-lg hover:bg-surface-sunken transition-colors"
        title="Clear date"
      >
        <X size={16} />
      </button>
    )}
  </div>
);
