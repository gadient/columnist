import React from 'react';

export const ChatMessageContent = ({ message }) => {
  const presentation = message.presentation;
  const warnings = Array.isArray(message.warnings) ? message.warnings : [];

  const WarningList = warnings.length > 0 ? (
    <div className="mb-2 space-y-1">
      {warnings.map((warning, idx) => (
        <div
          key={`${message.id}_warning_${idx}`}
          className="rounded-md border border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300 px-2 py-1 text-xs"
        >
          Warning: {warning}
        </div>
      ))}
    </div>
  ) : null;

  if (!presentation || message.role !== 'assistant') {
    return (
      <div>
        {WarningList}
        <div className="whitespace-pre-wrap">{message.text}</div>
      </div>
    );
  }

  if (presentation.format === 'table' && presentation.table && presentation.table.columns.length > 0) {
    return (
      <div className="space-y-2">
        {WarningList}
        {presentation.title && <div className="font-semibold text-fg">{presentation.title}</div>}
        <div className="overflow-x-auto rounded-lg border border-line">
          <table className="min-w-full text-xs">
            <thead className="bg-surface/60 text-fg-muted">
              <tr>
                {presentation.table.columns.map((column) => (
                  <th key={column} className="px-2 py-2 text-left font-semibold whitespace-nowrap">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="bg-surface/40">
              {presentation.table.rows.map((row, index) => (
                <tr key={`${message.id}_row_${index}`} className="border-t border-line align-top">
                  {row.map((cell, cellIndex) => (
                    <td key={`${message.id}_cell_${index}_${cellIndex}`} className="px-2 py-2 whitespace-pre-wrap">
                      {cell || '-'}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  if (presentation.format === 'bullets' && presentation.bullets?.length) {
    return (
      <div className="space-y-2">
        {WarningList}
        {presentation.title && <div className="font-semibold text-fg">{presentation.title}</div>}
        <ul className="list-disc pl-5 space-y-1">
          {presentation.bullets.map((bullet, index) => (
            <li key={`${message.id}_bullet_${index}`} className="whitespace-pre-wrap">{bullet}</li>
          ))}
        </ul>
      </div>
    );
  }

  return (
    <div>
      {WarningList}
      <div className="whitespace-pre-wrap">{presentation.text || message.text}</div>
    </div>
  );
};
