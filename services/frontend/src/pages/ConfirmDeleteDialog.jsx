// ConfirmDeleteDialog (M6): a modal confirmation before DELETE
// /macros/{name} fires -- no accidental one-click deletes. Deliberately a
// separate, focused step rather than a browser confirm() so it can show
// the macro's display_name and match the app's own visual language.

import Button from "../components/Button";
import Card from "../components/Card";

export default function ConfirmDeleteDialog({ macro, onConfirm, onCancel, deleting, error }) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-delete-title"
    >
      <Card className="w-full max-w-sm p-6 shadow-2xl shadow-black/40">
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
          <Button variant="secondary" onClick={onCancel} disabled={deleting}>
            Cancel
          </Button>
          <Button variant="danger" onClick={onConfirm} disabled={deleting}>
            {deleting ? "Deleting…" : "Delete"}
          </Button>
        </div>
      </Card>
    </div>
  );
}
