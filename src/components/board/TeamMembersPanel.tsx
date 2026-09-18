import React, { useState } from 'react';
import { Users, UserPlus, X } from 'lucide-react';
import { AVATAR_COLORS } from '../../constants/colors';
import { generateInitials } from '../../utils/strings';

interface TeamMembersPanelProps {
  members: any[];
  onAddMember: (member: any) => void;
  onDeleteMember: (id: any) => void;
}

export const TeamMembersPanel = ({ members, onAddMember, onDeleteMember }: TeamMembersPanelProps) => {
  const [showForm, setShowForm] = useState(false);
  const [newMember, setNewMember] = useState({ name: '', initials: '' });

  const closeForm = () => {
    setNewMember({ name: '', initials: '' });
    setShowForm(false);
  };

  const handleAdd = () => {
    if (!newMember.name.trim() || !newMember.initials.trim()) return;
    onAddMember({
      // A STRING id, not `Date.now()`. The snapshot schema types member ids as `str`
      // (`TeamMemberView.id`) and Pydantic v2 does not coerce int→str: a numeric id makes the whole
      // PUT /snapshot fail 422, and `updateBoard`'s catch reloads the board from the server, so the
      // new member would appear and then vanish. Same convention as card ids (see the KanbanView
      // note on randomUUID).
      id: crypto.randomUUID(),
      name: newMember.name.trim(),
      initials: newMember.initials.trim().toUpperCase(),
      color: AVATAR_COLORS[members.length % AVATAR_COLORS.length],
    });
    closeForm();
  };

  // From either input: Enter adds the member, Escape closes the form. The buttons below are the
  // discoverable path; these are the keys people reach for first.
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') closeForm();
    else if (e.key === 'Enter') handleAdd();
  };

  return (
    <div className="bg-surface rounded-xl p-4 border border-line">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-fg flex items-center gap-2">
          <Users size={18} className="text-fg-subtle" />
          Team Members
        </h3>
        <button
          onClick={() => setShowForm(v => !v)}
          className="flex items-center gap-1.5 text-sm text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300 transition-colors"
        >
          <UserPlus size={16} /> Add Member
        </button>
      </div>

      {showForm && (
        <div className="mb-3 p-3 bg-surface-raised rounded-lg flex gap-3 flex-wrap">
          <input
            type="text" placeholder="Full Name" value={newMember.name} autoFocus
            onChange={e => setNewMember({ name: e.target.value, initials: generateInitials(e.target.value) })}
            onKeyDown={handleKeyDown}
            className="flex-1 min-w-32 px-3 py-2 bg-surface-raised border border-line-strong rounded-lg text-sm text-fg placeholder:text-fg-subtle focus:ring-2 focus:ring-blue-500 outline-none"
          />
          <input
            type="text" placeholder="Initials" value={newMember.initials} maxLength={2}
            onChange={e => setNewMember(p => ({ ...p, initials: e.target.value.slice(0, 2).toUpperCase() }))}
            onKeyDown={handleKeyDown}
            className="w-20 px-3 py-2 bg-surface-raised border border-line-strong rounded-lg text-sm text-fg placeholder:text-fg-subtle focus:ring-2 focus:ring-blue-500 outline-none"
          />
          <button
            onClick={handleAdd}
            disabled={!newMember.name.trim() || !newMember.initials.trim()}
            className="px-4 py-2 bg-blue-600 text-fg-inverted rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            Add
          </button>
          <button
            onClick={closeForm}
            className="px-4 py-2 border border-line-strong rounded-lg text-sm text-fg-muted hover:text-fg hover:bg-surface transition-colors"
          >
            Cancel
          </button>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {members.map(member => (
          <div key={member.id} className="flex items-center gap-2 bg-surface-raised px-3 py-2 rounded-lg group">
            <div className={`w-7 h-7 rounded-full ${member.color} flex items-center justify-center text-fg-inverted text-xs font-bold`}>
              {member.initials}
            </div>
            <span className="text-sm text-fg-muted">{member.name}</span>
            <button
              onClick={() => onDeleteMember(member.id)}
              className="text-fg-muted hover:text-red-600 dark:hover:text-red-400 transition-colors opacity-0 group-hover:opacity-100 ml-1"
            >
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
};
