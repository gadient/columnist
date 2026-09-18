import React, { useState } from 'react';

export const ProjectEditForm = ({ initialTitle, initialDescription, onSave, onCancel }) => {
  const [title, setTitle] = useState(initialTitle);
  const [description, setDescription] = useState(initialDescription);

  const handleSave = () => {
    if (title.trim()) {
      onSave(title, description);
    }
  };

  return (
    <div className="bg-surface rounded-lg p-6 border-2 border-blue-400 shadow-md">
      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-fg-muted mb-2">Project Title</label>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="w-full px-4 py-3 text-2xl font-bold border border-line-strong rounded-md focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            placeholder="Enter project title"
            autoFocus
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-fg-muted mb-2">Project Description</label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className="w-full px-4 py-3 border border-line-strong rounded-md focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-none"
            placeholder="Describe your project (optional)"
            rows={3}
          />
        </div>
        <div className="flex gap-3 pt-2">
          <button
            onClick={handleSave}
            disabled={!title.trim()}
            className="bg-green-600 text-fg-inverted px-6 py-2 rounded-md hover:bg-green-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Save Changes
          </button>
          <button
            onClick={onCancel}
            className="bg-surface-sunken text-fg-muted px-6 py-2 rounded-md hover:bg-surface-raised transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
};
