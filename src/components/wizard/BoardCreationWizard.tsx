import React, { useState } from 'react';
import { Plus, X } from 'lucide-react';
import { BOARD_TEMPLATES } from '../../data/boardTemplates';
import { AVATAR_COLORS } from '../../constants/colors';
import { generateInitials } from '../../utils/strings';

export const BoardCreationWizard = ({ onCreateBoard, onCancel }) => {
  const [step, setStep] = useState(1);
  const [selectedTemplate, setSelectedTemplate] = useState(null);
  const [boardName, setBoardName] = useState('');
  const [columns, setColumns] = useState([]);
  const [teamMembers, setTeamMembers] = useState([]);

  const selectTemplate = (templateKey) => {
    const template = BOARD_TEMPLATES[templateKey];
    setSelectedTemplate(templateKey);
    setBoardName(template.name);
    setColumns([...template.columns]);
    setStep(2);
  };

  const updateColumnTitle = (index, newTitle) => {
    setColumns(prev => prev.map((col, i) =>
      i === index ? { ...col, title: newTitle } : col
    ));
  };

  const removeColumn = (index) => {
    if (columns.length > 1) {
      const columnName = columns[index]?.title || 'this column';
      const shouldDelete = window.confirm(
        `Remove "${columnName}" from this board?`
      );

      if (!shouldDelete) {
        return;
      }

      setColumns(prev => prev.filter((_, i) => i !== index));
    }
  };

  const addColumn = () => {
    setColumns(prev => [...prev, {
      id: `col_${Date.now()}`,
      title: 'New Column'
    }]);
  };

  const updateTeamMember = (index, field, value) => {
    setTeamMembers(prev => prev.map((member, i) => {
      if (i === index) {
        if (field === 'name') {
          return {
            ...member,
            name: value,
            initials: generateInitials(value)
          };
        }
        return { ...member, [field]: value };
      }
      return member;
    }));
  };

  const removeTeamMember = (index) => {
    const memberName = teamMembers[index]?.name || 'this team member';
    const shouldDelete = window.confirm(
      `Remove "${memberName}" from this board?`
    );

    if (!shouldDelete) {
      return;
    }

    setTeamMembers(prev => prev.filter((_, i) => i !== index));
  };

  const addTeamMember = () => {
    const newId = Math.max(...teamMembers.map(m => m.id), 0) + 1;
    const colorIndex = teamMembers.length % AVATAR_COLORS.length;

    setTeamMembers(prev => [...prev, {
      id: newId,
      name: '',
      initials: '',
      color: AVATAR_COLORS[colorIndex]
    }]);
  };

  const handleCreateBoard = () => {
    const payload = {
      title: boardName.trim(),
      description: 'Add your project description here',
      columns: columns.map(column => ({ title: column.title.trim() })),
      teamMembers: teamMembers.map(member => ({
        name: member.name.trim(),
        initials: member.initials.trim().toUpperCase(),
        color: member.color
      }))
    };

    onCreateBoard(payload);
  };

  if (step === 1) {
    return (
      <div className="mb-8 bg-surface rounded-lg p-6 shadow-sm border border-line">
        <h3 className="text-xl font-semibold text-fg mb-2">Choose a Template</h3>
        <p className="text-fg-muted mb-6">Step 1 of 3: Select a template to get started quickly</p>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
          {Object.entries(BOARD_TEMPLATES).map(([key, template]) => {
            const Icon = template.icon;
            return (
              <div
                key={key}
                onClick={() => selectTemplate(key)}
                className="border-2 border-line rounded-lg p-4 cursor-pointer hover:border-blue-400 hover:bg-blue-50 transition-all group"
              >
                <div className="flex items-start gap-3 mb-3">
                  <div className="p-2 bg-blue-100 rounded-lg group-hover:bg-blue-200 transition-colors">
                    <Icon size={24} className="text-blue-600" />
                  </div>
                  <div className="flex-1">
                    <h4 className="font-semibold text-fg mb-1">{template.name}</h4>
                    <p className="text-sm text-fg-muted">{template.description}</p>
                  </div>
                </div>
                <div className="flex gap-1 text-xs text-fg-muted">
                  {template.columns.map((col, idx) => (
                    <span key={idx} className="bg-surface-sunken px-2 py-1 rounded">
                      {col.title}
                    </span>
                  ))}
                </div>
              </div>
            );
          })}
        </div>

        <div className="flex justify-end">
          <button
            onClick={onCancel}
            className="bg-surface-sunken text-fg px-6 py-2 rounded-md hover:bg-surface-raised transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    );
  }

  if (step === 2) {
    return (
      <div className="mb-8 bg-surface rounded-lg p-6 shadow-sm border border-line">
        <h3 className="text-xl font-semibold text-fg mb-2">Customize Your Board</h3>
        <p className="text-fg-muted mb-6">Step 2 of 3: Set board name and customize columns</p>

        <div className="mb-6">
          <label className="block text-sm font-medium text-fg-muted mb-2">Board Name</label>
          <input
            type="text"
            value={boardName}
            onChange={(e) => setBoardName(e.target.value)}
            className="w-full px-4 py-2 border border-line-strong rounded-md focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            placeholder="Enter board name"
          />
        </div>

        <div className="mb-6">
          <h4 className="text-sm font-medium text-fg-muted mb-3">Columns</h4>
          <div className="space-y-2 max-h-64 overflow-y-auto">
            {columns.map((column, index) => (
              <div key={index} className="flex gap-2 items-center">
                <input
                  type="text"
                  value={column.title}
                  onChange={(e) => updateColumnTitle(index, e.target.value)}
                  className="flex-1 px-3 py-2 border border-line-strong rounded-md text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  placeholder="Column name"
                />
                {columns.length > 1 && (
                  <button
                    onClick={() => removeColumn(index)}
                    className="text-red-500 hover:text-red-700 transition-colors"
                    title="Remove column"
                  >
                    <X size={16} />
                  </button>
                )}
              </div>
            ))}
          </div>

          <button
            onClick={addColumn}
            className="mt-3 flex items-center gap-2 text-blue-600 hover:text-blue-700 transition-colors text-sm"
          >
            <Plus size={16} />
            Add Column
          </button>
        </div>

        <div className="flex gap-3">
          <button
            onClick={() => setStep(1)}
            className="bg-surface-sunken text-fg px-6 py-2 rounded-md hover:bg-surface-raised transition-colors"
          >
            Back
          </button>
          <button
            onClick={() => setStep(3)}
            disabled={!boardName.trim() || columns.length === 0 || columns.some(col => !col.title.trim())}
            className="bg-blue-600 text-fg-inverted px-6 py-2 rounded-md hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Next
          </button>
          <button
            onClick={onCancel}
            className="bg-surface-sunken text-fg px-6 py-2 rounded-md hover:bg-surface-raised transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    );
  }

  if (step === 3) {
    return (
      <div className="mb-8 bg-surface rounded-lg p-6 shadow-sm border border-line">
        <h3 className="text-xl font-semibold text-fg mb-2">Add Team Members</h3>
        <p className="text-fg-muted mb-6">Step 3 of 3: Add team members (optional)</p>

        <div className="mb-6">
          {teamMembers.length > 0 ? (
            <div className="space-y-3 max-h-96 overflow-y-auto mb-4">
              {teamMembers.map((member, index) => (
                <div key={index} className="flex gap-3 items-center bg-surface-sunken p-3 rounded-lg">
                  <div className={`w-10 h-10 rounded-full ${member.color} flex items-center justify-center text-fg-inverted text-sm font-medium`}>
                    {member.initials || '?'}
                  </div>
                  <div className="flex-1 grid grid-cols-2 gap-3">
                    <input
                      type="text"
                      value={member.name}
                      onChange={(e) => updateTeamMember(index, 'name', e.target.value)}
                      className="px-3 py-2 border border-line-strong rounded-md text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                      placeholder="Full name"
                    />
                    <input
                      type="text"
                      value={member.initials}
                      onChange={(e) => updateTeamMember(index, 'initials', e.target.value.slice(0, 3).toUpperCase())}
                      className="px-3 py-2 border border-line-strong rounded-md text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                      placeholder="Initials"
                      maxLength={3}
                    />
                  </div>
                  <button
                    onClick={() => removeTeamMember(index)}
                    className="text-red-500 hover:text-red-700 transition-colors"
                    title="Remove team member"
                  >
                    <X size={16} />
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-fg-muted text-sm mb-4 italic">No team members added yet.</p>
          )}

          <button
            onClick={addTeamMember}
            className="flex items-center gap-2 text-blue-600 hover:text-blue-700 transition-colors text-sm"
          >
            <Plus size={16} />
            Add Team Member
          </button>
        </div>

        <div className="flex gap-3">
          <button
            onClick={() => setStep(2)}
            className="bg-surface-sunken text-fg px-6 py-2 rounded-md hover:bg-surface-raised transition-colors"
          >
            Back
          </button>
          <button
            onClick={handleCreateBoard}
            disabled={teamMembers.some(member => !member.name.trim() || !member.initials.trim())}
            className="bg-green-600 text-fg-inverted px-6 py-2 rounded-md hover:bg-green-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Create Board
          </button>
          <button
            onClick={onCancel}
            className="bg-surface-sunken text-fg px-6 py-2 rounded-md hover:bg-surface-raised transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    );
  }
};
