import React from 'react';
import { Grid3X3, Calendar, Target, List, Flame } from 'lucide-react';

const VIEWS = [
  { id: 'kanban',   name: 'Kanban',   icon: Grid3X3 },
  { id: 'timeline', name: 'Timeline', icon: Calendar },
  { id: 'matrix',   name: 'Matrix',   icon: Target },
  { id: 'feed',     name: 'Feed',     icon: List },
  { id: 'focus',    name: 'Focus',    icon: Flame },
];

export const ViewSwitcher = ({ currentView, onViewChange }) => (
  <div className="flex gap-1 bg-surface p-1.5 rounded-xl overflow-x-auto">
    {VIEWS.map(view => {
      const Icon = view.icon;
      return (
        <button
          key={view.id}
          onClick={() => onViewChange(view.id)}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-all text-sm font-medium whitespace-nowrap ${
            currentView === view.id
              ? 'bg-gradient-to-r from-blue-500 to-purple-500 text-fg-inverted shadow-lg'
              : 'text-fg-subtle hover:text-fg hover:bg-surface-raised'
          }`}
        >
          <Icon size={16} />
          <span>{view.name}</span>
        </button>
      );
    })}
  </div>
);
