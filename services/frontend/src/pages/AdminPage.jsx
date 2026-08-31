// AdminPage (M7): admin-only user-management dashboard -- lists every
// account (GET /users), creates new ones (POST /users), and edits/deletes
// existing ones (PUT/DELETE /users/{id}). See
// docs/decisions/013-per-user-accounts.md for the backend this drives.
//
// Reachable only via Sidebar's Admin nav item (rendered only for an admin
// session) and guarded again at the route level in App.jsx -- this page
// assumes session.role === "admin" and never checks it itself.
//
// Follows HistoryPage's table shape (load/error/skeleton/empty states,
// Card-wrapped <table>) and ConfirmDeleteDialog's modal pattern for
// delete confirmation, so this reads as the same app, not a bolted-on
// admin panel.

import { useCallback, useEffect, useState } from "react";
import { UserPlus, Pencil, Trash2, X } from "lucide-react";
import { useAuth } from "../auth/AuthContext";
import { useProtectedApi } from "../auth/useProtectedApi";
import { listUsers, createUser, updateUser, deleteUser } from "../api/client";
import Shell from "../components/Shell";
import TopBar from "../components/TopBar";
import Button from "../components/Button";
import Card from "../components/Card";
import Skeleton from "../components/Skeleton";

const ROLES = ["admin", "employee"];

export default function AdminPage({ page, onNavigate }) {
  const { session } = useAuth();
  const callProtected = useProtectedApi();

  const [users, setUsers] = useState(null); // null = still loading
  const [listError, setListError] = useState(null);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [editingUserId, setEditingUserId] = useState(null);
  const [userPendingDelete, setUserPendingDelete] = useState(null); // UserOut | null

  const refreshUsers = useCallback(async () => {
    setListError(null);
    try {
      const result = await callProtected(() => listUsers(session.token));
      setUsers(result);
    } catch (err) {
      setListError(err.message);
    }
  }, [callProtected, session.token]);

  useEffect(() => {
    refreshUsers();
  }, [refreshUsers]);

  return (
    <Shell page={page} onNavigate={onNavigate}>
      <TopBar
        title="User management"
        description="Real per-user accounts -- admin-managed only, no self-service registration."
        action={
          <Button onClick={() => setShowCreateForm((v) => !v)} className="active:scale-[0.97]">
            {showCreateForm ? (
              "Close"
            ) : (
              <>
                <UserPlus className="h-4 w-4" strokeWidth={2} />
                New user
              </>
            )}
          </Button>
        }
      />

      <main className="mx-auto max-w-4xl px-8 pb-10 space-y-4">
        {showCreateForm && (
          <CreateUserCard
            token={session.token}
            callProtected={callProtected}
            onClose={() => setShowCreateForm(false)}
            onCreated={() => {
              setShowCreateForm(false);
              refreshUsers();
            }}
          />
        )}

        <UsersTable
          users={users}
          error={listError}
          editingUserId={editingUserId}
          token={session.token}
          callProtected={callProtected}
          onEdit={setEditingUserId}
          onCancelEdit={() => setEditingUserId(null)}
          onSaved={() => {
            setEditingUserId(null);
            refreshUsers();
          }}
          onDelete={setUserPendingDelete}
        />
      </main>

      {userPendingDelete && (
        <ConfirmDeleteUserDialog
          user={userPendingDelete}
          token={session.token}
          callProtected={callProtected}
          onCancel={() => setUserPendingDelete(null)}
          onDeleted={() => {
            setUserPendingDelete(null);
            refreshUsers();
          }}
        />
      )}
    </Shell>
  );
}

function CreateUserCard({ token, callProtected, onClose, onCreated }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("employee");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  async function handleSubmit(event) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await callProtected(() => createUser(token, { username, password, role }));
      onCreated();
    } catch (err) {
      setError(err.message || "failed to create user");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card level="accent" as="form" onSubmit={handleSubmit} className="p-6">
      <div className="mb-5 flex items-center justify-between">
        <h2 className="text-sm font-medium text-signal-100">New user</h2>
        <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close">
          <X className="h-4 w-4" strokeWidth={1.75} />
        </Button>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Username" id="new-username">
          <input
            id="new-username"
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="e.g. jsmith"
            autoComplete="off"
            required
            className={INPUT_CLASSES}
          />
        </Field>

        <Field label="Password" id="new-password">
          <input
            id="new-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
            required
            className={INPUT_CLASSES}
          />
        </Field>

        <Field label="Role" id="new-role">
          <select
            id="new-role"
            value={role}
            onChange={(e) => setRole(e.target.value)}
            className={INPUT_CLASSES}
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </Field>
      </div>

      {error && <ErrorBanner className="mt-4">{error}</ErrorBanner>}

      <Button type="submit" disabled={submitting} className="mt-5">
        {submitting ? "Creating…" : "Create user"}
      </Button>
    </Card>
  );
}

function UsersTable({
  users,
  error,
  editingUserId,
  token,
  callProtected,
  onEdit,
  onCancelEdit,
  onSaved,
  onDelete,
}) {
  if (error) {
    return <ErrorBanner>{error}</ErrorBanner>;
  }

  if (users === null) {
    return <Skeleton count={3} height="h-12" />;
  }

  if (users.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-signal-700 px-6 py-12 text-center">
        <p className="text-sm text-signal-400">No users yet.</p>
      </div>
    );
  }

  return (
    <Card className="overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-signal-700 text-xs text-signal-400">
              <th className="px-4 py-2.5 font-medium">Username</th>
              <th className="px-4 py-2.5 font-medium">Role</th>
              <th className="px-4 py-2.5 font-medium">Created</th>
              <th className="px-4 py-2.5 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {users.map((user) =>
              editingUserId === user.id ? (
                <EditUserRow
                  key={user.id}
                  user={user}
                  token={token}
                  callProtected={callProtected}
                  onCancel={onCancelEdit}
                  onSaved={onSaved}
                />
              ) : (
                <UserRow key={user.id} user={user} onEdit={onEdit} onDelete={onDelete} />
              )
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function UserRow({ user, onEdit, onDelete }) {
  return (
    <tr className="group border-b border-signal-700 last:border-0 hover:bg-signal-800/60">
      <td className="px-4 py-3 font-medium text-signal-100">{user.username}</td>
      <td className="px-4 py-3">
        <RoleBadge role={user.role} />
      </td>
      <td className="px-4 py-3 text-signal-400">{formatCreatedAt(user.created_at)}</td>
      <td className="px-4 py-3">
        <div className="flex justify-end gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
          <Button
            variant="ghost"
            size="icon"
            title="Edit"
            aria-label={`Edit ${user.username}`}
            onClick={() => onEdit(user.id)}
          >
            <Pencil className="h-3.5 w-3.5" strokeWidth={1.75} />
          </Button>
          <Button
            variant="ghost-danger"
            size="icon"
            title="Delete"
            aria-label={`Delete ${user.username}`}
            onClick={() => onDelete(user)}
          >
            <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} />
          </Button>
        </div>
      </td>
    </tr>
  );
}

function EditUserRow({ user, token, callProtected, onCancel, onSaved }) {
  const [role, setRole] = useState(user.role);
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  async function handleSave() {
    setError(null);
    setSubmitting(true);
    try {
      await callProtected(() =>
        updateUser(token, user.id, {
          role,
          password: password.trim() ? password : undefined,
        })
      );
      onSaved();
    } catch (err) {
      setError(err.message || "failed to update user");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <tr className="border-b border-signal-700 last:border-0 bg-signal-800/40">
      <td className="px-4 py-3 font-medium text-signal-100" colSpan={4}>
        <div className="flex flex-wrap items-end gap-3">
          <span className="pb-2 text-sm">{user.username}</span>

          <Field label="Role" id={`edit-role-${user.id}`}>
            <select
              id={`edit-role-${user.id}`}
              value={role}
              onChange={(e) => setRole(e.target.value)}
              className={`${INPUT_CLASSES} w-32`}
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Reset password (optional)" id={`edit-password-${user.id}`}>
            <input
              id={`edit-password-${user.id}`}
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="leave blank to keep current"
              autoComplete="new-password"
              className={`${INPUT_CLASSES} w-56`}
            />
          </Field>

          <div className="flex gap-2 pb-0.5">
            <Button variant="secondary" size="sm" onClick={onCancel} disabled={submitting}>
              Cancel
            </Button>
            <Button size="sm" onClick={handleSave} disabled={submitting}>
              {submitting ? "Saving…" : "Save"}
            </Button>
          </div>
        </div>

        {error && <ErrorBanner className="mt-3">{error}</ErrorBanner>}
      </td>
    </tr>
  );
}

function ConfirmDeleteUserDialog({ user, token, callProtected, onCancel, onDeleted }) {
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState(null);

  async function handleConfirm() {
    setDeleting(true);
    setError(null);
    try {
      await callProtected(() => deleteUser(token, user.id));
      onDeleted();
    } catch (err) {
      setError(err.message || "failed to delete user");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-delete-user-title"
    >
      <Card className="w-full max-w-sm p-6 shadow-2xl shadow-black/40">
        <h2 id="confirm-delete-user-title" className="text-sm font-medium text-signal-100">
          Delete {user.username}?
        </h2>
        <p className="mt-2 text-sm text-signal-400">This can&apos;t be undone.</p>

        {error && <ErrorBanner className="mt-4">{error}</ErrorBanner>}

        <div className="mt-6 flex justify-end gap-2">
          <Button variant="secondary" onClick={onCancel} disabled={deleting}>
            Cancel
          </Button>
          <Button variant="danger" onClick={handleConfirm} disabled={deleting}>
            {deleting ? "Deleting…" : "Delete"}
          </Button>
        </div>
      </Card>
    </div>
  );
}

function RoleBadge({ role }) {
  const isAdmin = role === "admin";
  const style = isAdmin
    ? "border-amber-500/30 bg-amber-500/10 text-amber-500"
    : "border-signal-600 bg-signal-800 text-signal-300";
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium capitalize ${style}`}
    >
      {role}
    </span>
  );
}

function Field({ label, id, children }) {
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-xs font-medium text-signal-400">
        {label}
      </label>
      {children}
    </div>
  );
}

function ErrorBanner({ children, className = "" }) {
  return (
    <div
      role="alert"
      className={`flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger ${className}`}
    >
      {children}
    </div>
  );
}

const INPUT_CLASSES =
  "w-full rounded-lg border border-signal-600 bg-signal-800 px-3 py-2 text-sm text-signal-100 placeholder-signal-400 outline-none transition-colors focus:border-amber-500 focus:ring-1 focus:ring-amber-500";

function formatCreatedAt(isoString) {
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return isoString;
  return date.toLocaleString();
}
