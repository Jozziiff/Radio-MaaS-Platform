// ConfirmDeleteDialog (M6): a modal confirmation before DELETE
// /macros/{name} fires -- no accidental one-click deletes. Deliberately a
// separate, focused step rather than a browser confirm() so it can show
// the macro's display_name and match the app's own visual language.

export default function ConfirmDeleteDialog({ macro, onConfirm, onCancel, deleting, error }) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-delete-title"
    >
      <div className="w-full max-w-sm rounded-xl border border-signal-700 bg-signal-900 p-6 shadow-2xl shadow-black/40">
        <h2 id="confirm-delete-title" className="text-sm font-medium text-signal-100">
          Delete {macro.display_name}?
        </h2>
        <p className="mt-2 text-sm text-signal-400">This can&apos;t be undone.</p>

        {error && (
          <div
            role="alert"
            className="mt-4 flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger"
          >
            {error}
          </div>
        )}

        <div className="mt-6 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={deleting}
            className="rounded-lg border border-signal-600 px-3 py-2 text-sm text-signal-200 transition-colors hover:bg-signal-800 disabled:cursor-not-allowed disabled:opacity-60"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={deleting}
            className="rounded-lg bg-danger px-3 py-2 text-sm font-medium text-signal-950 transition-colors hover:bg-danger/85 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {deleting ? "Deleting…" : "Delete"}
          </button>
        </div>
      </div>
    </div>
  );
}
