import React from 'react';

export const DeleteConfirmationModal = ({ show, itemName, onConfirm, onCancel }) => {
  if (!show) {
    return null;
  }

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-surface rounded-lg p-6 max-w-md w-full mx-4">
        <h3 className="text-lg font-semibold text-fg mb-2">
          Confirm Deletion
        </h3>
        <p className="text-fg-muted mb-4">
          Deleting <span className="font-semibold">"{itemName}"</span> will remove all data and cannot be undone. Do you want to proceed?
        </p>
        <div className="flex justify-end gap-3">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-fg-muted bg-surface-sunken rounded-md hover:bg-surface-raised transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-2 bg-red-600 text-fg-inverted rounded-md hover:bg-red-700 transition-colors"
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  );
};
