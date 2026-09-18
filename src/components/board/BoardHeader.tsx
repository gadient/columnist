import React from 'react';
import { Edit3 } from 'lucide-react';
import { ProjectEditForm } from './ProjectEditForm';

interface BoardHeaderProps {
  projectInfo: { title: string; description: string };
  editing: boolean;
  onStartEdit: () => void;
  onSave: (title: string, description: string) => void;
  onCancel: () => void;
}

export const BoardHeader = ({ projectInfo, editing, onStartEdit, onSave, onCancel }: BoardHeaderProps) => {
  if (editing) {
    return (
      <ProjectEditForm
        initialTitle={projectInfo.title}
        initialDescription={projectInfo.description}
        onSave={onSave}
        onCancel={onCancel}
      />
    );
  }
  return (
    <div className="group cursor-pointer" onClick={onStartEdit}>
      <div className="flex items-center gap-3 mb-1">
        <h1 className="text-3xl font-bold text-fg">{projectInfo.title}</h1>
        <Edit3 size={18} className="text-fg-muted opacity-0 group-hover:opacity-100 transition-opacity" />
      </div>
      <p className="text-fg-subtle">{projectInfo.description}</p>
    </div>
  );
};
