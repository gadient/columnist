import React from 'react';

type Priority = 'low' | 'medium' | 'high';

export const PriorityPicker = ({ value, onChange }: { value: Priority; onChange: (p: Priority) => void }) => (
  <div className="flex gap-1.5">
    {(['low', 'medium', 'high'] as Priority[]).map(p => (
      <button
        key={p}
        onClick={() => onChange(p)}
        className={`flex-1 px-3 py-2 rounded-lg text-xs font-semibold transition-all ${
          value === p
            ? p === 'low' ? 'bg-green-500 text-fg-inverted shadow' : p === 'medium' ? 'bg-yellow-500 text-fg-inverted shadow' : 'bg-red-500 text-fg-inverted shadow'
            : 'bg-surface-raised text-fg-subtle hover:bg-surface-raised'
        }`}
      >
        {p === 'low' ? '🟢 Low' : p === 'medium' ? '🟡 Med' : '🔴 High'}
      </button>
    ))}
  </div>
);
