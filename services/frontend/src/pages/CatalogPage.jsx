// Catalog page (M6): lists built macros (GET /macros), and lets a user
// build a new one, edit an existing one, run one against an uploaded CSV,
// or delete one -- all through the shared api/client.js. Create and edit
// both go through the same MacroForm (POST /macros/{name}/build upserts
// either way); running goes through RunPanel.
//
// Layout animation (motion's `layout` prop): editing or running a macro
// doesn't open a separate panel -- its own card expands in place into the
// form/panel, and every other card in the grid smoothly reflows around it
// (motion tracks each card's position/size via layoutId and animates the
// delta, a FLIP animation, rather than the browser just snapping the grid
// to its new track sizes). "New macro" uses the same expand-in-place
// pattern as a dedicated card at the top of the grid, so create, edit, and
// run all feel like one consistent interaction, not three different UI
// patterns.
//
// `expanded` state shape: null | {type: "create"} | {type: "edit"|"run", technicalName}.
// A bare technical_name string isn't enough once a card can be expanded
// two different ways (edit vs. run) -- the `type` disambiguates which
// panel a given card should show.

import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { useAuth } from "../auth/AuthContext";
import { useProtectedApi } from "../auth/useProtectedApi";
import { listMacros, getMacro, deleteMacro } from "../api/client";
import { iconComponentFor } from "../icons";
import MacroForm from "./MacroForm";
import RunPanel from "./RunPanel";
import ConfirmDeleteDialog from "./ConfirmDeleteDialog";
import Nav from "./Nav";
import { Pencil, Trash2, Plus, Play, ExternalLink, GitBranch } from "lucide-react";

const CARD_TRANSITION = { type: "spring", stiffness: 420, damping: 38, mass: 0.7 };

export default function CatalogPage({ page, onNavigate }) {
  const { session } = useAuth();
  const callProtected = useProtectedApi();

  const [macros, setMacros] = useState(null); // null = still loading
  const [catalogError, setCatalogError] = useState(null);

  const [expanded, setExpanded] = useState(null); // see module comment for shape
  const [editValues, setEditValues] = useState(null); // MacroDetail once loaded, for the expanded edit card
  const [editLoadError, setEditLoadError] = useState(null);

  const [macroPendingDelete, setMacroPendingDelete] = useState(null); // BuiltMacro | null
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);

  const refreshCatalog = useCallback(async () => {
    setCatalogError(null);
    try {
      const result = await callProtected(() => listMacros(session.token));
      setMacros(result);
    } catch (err) {
      setCatalogError(err.message);
    }
  }, [callProtected, session.token]);

  useEffect(() => {
    refreshCatalog();
  }, [refreshCatalog]);

  function closeExpanded() {
    setExpanded(null);
    setEditValues(null);
    setEditLoadError(null);
  }

  function toggleCreate() {
    if (expanded?.type === "create") {
      closeExpanded();
    } else {
      setExpanded({ type: "create" });
      setEditValues(null);
    }
  }

  async function openEdit(technicalName) {
    if (expanded?.type === "edit" && expanded.technicalName === technicalName) {
      closeExpanded();
      return;
    }
    setExpanded({ type: "edit", technicalName });
    setEditValues(null);
    setEditLoadError(null);
    try {
      const detail = await callProtected(() => getMacro(session.token, technicalName));
      setEditValues({
        technicalName: detail.technical_name,
        displayName: detail.display_name,
        description: detail.description ?? "",
        icon: detail.icon,
        sourceCode: detail.source_code,
      });
    } catch (err) {
      setEditLoadError(err.message);
    }
  }

  function toggleRun(technicalName) {
    if (expanded?.type === "run" && expanded.technicalName === technicalName) {
      closeExpanded();
    } else {
      setExpanded({ type: "run", technicalName });
      setEditValues(null);
    }
  }

  async function confirmDelete() {
    setDeleting(true);
    setDeleteError(null);
    try {
      await callProtected(() => deleteMacro(session.token, macroPendingDelete.technical_name));
      setMacroPendingDelete(null);
      if (expanded?.technicalName === macroPendingDelete.technical_name) closeExpanded();
      await refreshCatalog();
    } catch (err) {
      setDeleteError(err.message);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="min-h-screen bg-signal-950">
      <Nav page={page} onNavigate={onNavigate} giteaLink={<GiteaInstanceLink macros={macros} />} />

      <main className="mx-auto max-w-4xl px-6 py-10">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-lg font-medium text-signal-100">Macro catalog</h1>
            <p className="mt-1 text-sm text-signal-400">
              Built macros ready to run, and a place to build new ones.
            </p>
          </div>
          <button
            onClick={toggleCreate}
            className="flex shrink-0 items-center gap-1.5 rounded-lg bg-amber-500 px-4 py-2 text-sm font-medium text-signal-950 transition-colors hover:bg-amber-400 active:scale-[0.97]"
          >
            {expanded?.type === "create" ? (
              "Close"
            ) : (
              <>
                <Plus className="h-4 w-4" strokeWidth={2} />
                New macro
              </>
            )}
          </button>
        </div>

        <CatalogGrid
          macros={macros}
          error={catalogError}
          expanded={expanded}
          editValues={editValues}
          editLoadError={editLoadError}
          token={session.token}
          callProtected={callProtected}
          onEdit={openEdit}
          onRun={toggleRun}
          onDelete={setMacroPendingDelete}
          onClose={closeExpanded}
          onBuilt={() => {
            closeExpanded();
            refreshCatalog();
          }}
        />
      </main>

      {macroPendingDelete && (
        <ConfirmDeleteDialog
          macro={macroPendingDelete}
          deleting={deleting}
          error={deleteError}
          onCancel={() => {
            setMacroPendingDelete(null);
            setDeleteError(null);
          }}
          onConfirm={confirmDelete}
        />
      )}
    </div>
  );
}

function CatalogGrid({
  macros,
  error,
  expanded,
  editValues,
  editLoadError,
  token,
  callProtected,
  onEdit,
  onRun,
  onDelete,
  onClose,
  onBuilt,
}) {
  if (error) {
    return (
      <div
        role="alert"
        className="flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger"
      >
        {error}
      </div>
    );
  }

  if (macros === null) {
    return (
      <div className="grid gap-3 sm:grid-cols-2">
        {[0, 1].map((i) => (
          <motion.div
            key={i}
            animate={{ opacity: [0.4, 0.8, 0.4] }}
            transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut", delay: i * 0.15 }}
            className="h-26 rounded-xl border border-signal-700 bg-signal-900"
          />
        ))}
      </div>
    );
  }

  if (macros.length === 0 && expanded?.type !== "create") {
    return (
      <motion.div
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        className="rounded-xl border border-dashed border-signal-700 px-6 py-12 text-center"
      >
        <p className="text-sm text-signal-400">No macros yet — build one to get started.</p>
      </motion.div>
    );
  }

  const expandedMacro = macros.find((m) => m.technical_name === expanded?.technicalName);

  return (
    <motion.div layout className="grid gap-3 sm:grid-cols-2">
      <AnimatePresence initial={false}>
        {expanded?.type === "create" && (
          <ExpandedFormCard key="__create" layoutId="__create">
            <MacroForm
              token={token}
              callProtected={callProtected}
              title="New macro"
              onCancel={onClose}
              technicalNameEditable
              submitLabel="Build macro"
              submittingLabel="Building…"
              onBuilt={onBuilt}
            />
          </ExpandedFormCard>
        )}

        {macros.map((macro) => {
          if (expanded?.type === "edit" && expanded.technicalName === macro.technical_name) {
            return (
              <ExpandedFormCard key={macro.technical_name} layoutId={macro.technical_name}>
                {editLoadError ? (
                  <div className="p-6">
                    <div
                      role="alert"
                      className="flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger"
                    >
                      {editLoadError}
                    </div>
                  </div>
                ) : editValues ? (
                  <MacroForm
                    token={token}
                    callProtected={callProtected}
                    title={`Edit ${expandedMacro?.display_name ?? macro.technical_name}`}
                    onCancel={onClose}
                    technicalNameEditable={false}
                    initialValues={editValues}
                    submitLabel="Save changes"
                    submittingLabel="Saving…"
                    onBuilt={onBuilt}
                  />
                ) : (
                  <p className="p-6 text-sm text-signal-400">Loading…</p>
                )}
              </ExpandedFormCard>
            );
          }

          if (expanded?.type === "run" && expanded.technicalName === macro.technical_name) {
            return (
              <ExpandedFormCard key={macro.technical_name} layoutId={macro.technical_name}>
                <RunPanel
                  token={token}
                  callProtected={callProtected}
                  macro={macro}
                  onClose={onClose}
                />
              </ExpandedFormCard>
            );
          }

          return (
            <MacroCard
              key={macro.technical_name}
              macro={macro}
              onEdit={onEdit}
              onRun={onRun}
              onDelete={onDelete}
            />
          );
        })}
      </AnimatePresence>
    </motion.div>
  );
}

function ExpandedFormCard({ layoutId, children }) {
  return (
    <motion.div
      layoutId={layoutId}
      layout
      initial={{ opacity: 0, scale: 0.98 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.98 }}
      transition={CARD_TRANSITION}
      className="col-span-full overflow-hidden rounded-xl border border-amber-500/40 bg-signal-900 shadow-xl shadow-black/30"
    >
      {children}
    </motion.div>
  );
}

function MacroCard({ macro, onEdit, onRun, onDelete }) {
  const Icon = iconComponentFor(macro.icon);

  return (
    <motion.div
      layoutId={macro.technical_name}
      layout
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={CARD_TRANSITION}
      className="group relative rounded-xl border border-signal-700 bg-signal-900 p-4 transition-colors hover:border-amber-500/40 hover:bg-signal-800/60"
    >
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-amber-500/30 bg-amber-500/10 text-amber-500 transition-colors group-hover:border-amber-500/60 group-hover:bg-amber-500/15">
          <Icon className="h-4 w-4" strokeWidth={1.75} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-signal-100">{macro.display_name}</p>
          <p className="font-mono text-xs text-signal-400">{macro.technical_name}</p>
        </div>

        {/* Edit/Delete: hidden until the card is hovered (or focused, for
            keyboard users), so the catalog stays visually calm at rest. */}
        <div className="flex shrink-0 gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
          <button
            type="button"
            title="Edit"
            aria-label={`Edit ${macro.display_name}`}
            onClick={() => onEdit(macro.technical_name)}
            className="rounded-md p-1.5 text-signal-400 transition-colors hover:bg-signal-700 hover:text-signal-100"
          >
            <Pencil className="h-3.5 w-3.5" strokeWidth={1.75} />
          </button>
          <button
            type="button"
            title="Delete"
            aria-label={`Delete ${macro.display_name}`}
            onClick={() => onDelete(macro)}
            className="rounded-md p-1.5 text-signal-400 transition-colors hover:bg-danger/15 hover:text-danger"
          >
            <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} />
          </button>
        </div>
      </div>

      {macro.description && <p className="mt-3 text-xs text-signal-400">{macro.description}</p>}
      <p className="mt-3 font-mono text-xs text-signal-400">{macro.image_tag}</p>
      <div className="mt-3 flex items-center justify-between gap-2">
        <p className="text-xs text-signal-400">Built {formatBuiltAt(macro.built_at)}</p>
        <div className="flex shrink-0 items-center gap-2">
          <GiteaRepoLink url={macro.gitea_repo_url} />
          <button
            type="button"
            onClick={() => onRun(macro.technical_name)}
            className="flex shrink-0 items-center gap-1.5 rounded-md border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 text-xs font-medium text-amber-500 transition-colors hover:border-amber-500/60 hover:bg-amber-500/15"
          >
            <Play className="h-3 w-3" strokeWidth={2} fill="currentColor" />
            Run
          </button>
        </div>
      </div>
    </motion.div>
  );
}

// gitea_repo_url is null for a macro built before this feature existed, or
// one whose Gitea mirror failed silently on build (see
// docs/decisions/005-gitea-artifact-mirror.md's fail-open behavior) --
// rendered as a disabled, non-clickable equivalent instead of an <a> with
// no href (which would be a dead link, or worse, href="undefined").
function GiteaRepoLink({ url }) {
  if (!url) {
    return (
      <span
        title="Not yet pushed to Gitea"
        className="flex shrink-0 cursor-not-allowed items-center gap-1 rounded-md px-2 py-1 text-xs text-signal-600"
      >
        <ExternalLink className="h-3 w-3" strokeWidth={1.75} />
        View in Gitea
      </span>
    );
  }

  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      title="View this macro's mirrored repo in Gitea"
      className="flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs text-signal-400 transition-colors hover:bg-signal-700 hover:text-signal-100"
    >
      <ExternalLink className="h-3 w-3" strokeWidth={1.75} />
      View in Gitea
    </a>
  );
}

// The instance-wide repo listing (e.g. http://gitea:3000/admin) isn't a
// value GET /macros returns directly -- only each macro's own full repo
// URL (.../admin/rtwp-anomaly-demo) is. Deriving the owner-level URL by
// dropping the last path segment avoids a second backend round trip or a
// new config value just for this one navbar link; see
// docs/decisions/005-gitea-artifact-mirror.md for why GITEA_URL itself
// isn't otherwise exposed to the frontend.
function giteaInstanceUrlFrom(macros) {
  const mirrored = macros?.find((m) => m.gitea_repo_url);
  if (!mirrored) return null;
  const url = new URL(mirrored.gitea_repo_url);
  const segments = url.pathname.split("/").filter(Boolean);
  segments.pop();
  url.pathname = segments.join("/");
  return url.toString();
}

function GiteaInstanceLink({ macros }) {
  const href = giteaInstanceUrlFrom(macros);

  if (!href) {
    return (
      <span
        title="No macro has been pushed to Gitea yet"
        className="flex items-center gap-1.5 text-sm text-signal-600"
      >
        <GitBranch className="h-3.5 w-3.5" strokeWidth={1.75} />
        Gitea
      </span>
    );
  }

  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title="Browse all mirrored macros in Gitea"
      className="flex items-center gap-1.5 text-sm text-signal-400 transition-colors hover:text-signal-100"
    >
      <GitBranch className="h-3.5 w-3.5" strokeWidth={1.75} />
      Gitea
    </a>
  );
}

function formatBuiltAt(isoString) {
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return isoString;
  return date.toLocaleString();
}
