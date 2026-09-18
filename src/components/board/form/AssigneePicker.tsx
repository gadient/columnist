import React from 'react';

interface AssigneePickerProps {
  members: any[];
  selected: any[];
  onChange: (id: any) => void;
}

export const AssigneePicker = ({ members, selected, onChange }: AssigneePickerProps) => (
  <div className="flex flex-wrap gap-2">
    {members.map(member => (
      <button
        key={member.id}
        onClick={() => onChange(member.id)}
        className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-all ${
          selected.includes(member.id)
            ? `${member.color} text-fg-inverted shadow scale-105`
            : 'bg-surface-raised text-fg-muted hover:bg-surface-raised'
        }`}
      >
        <div className={`w-5 h-5 rounded-full ${selected.includes(member.id) ? 'bg-white/30' : member.color} flex items-center justify-center text-fg-inverted text-[10px] font-bold`}>
          {member.initials[0]}
        </div>
        {member.name.split(' ')[0]}
      </button>
    ))}
  </div>
);
