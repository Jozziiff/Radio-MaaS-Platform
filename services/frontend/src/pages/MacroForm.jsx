// MacroForm (M6): the create/edit macro panel, shared by NewMacroForm and
// EditMacroForm since both submit to the same POST /macros/{name}/build
// endpoint (build_macro upserts -- a "rebuild" and an "edit" are the same
// request shape on the backend). The only real difference between the two
// callers is whether technical_name is editable and whether fields start
// pre-filled -- both are just props here, not two separate forms.

import { useState } from "react";
import { X, TriangleAlert } from "lucide-react";
import { buildMacro, MacroSyntaxError, BuildFailedError } from "../api/client";
import IconPicker from "./IconPicker";
import Button from "../components/Button";

export default function MacroForm({
  token,
  callProtected,
  onBuilt,
  onCancel,
  title,
  technicalNameEditable = true,
  initialValues = null,
  submitLabel = "Build macro",
  submittingLabel = "Building…",
}) {
  const [technicalName, setTechnicalName] = useState(initialValues?.technicalName ?? "");
  const [displayName, setDisplayName] = useState(initialValues?.displayName ?? "");
  const [description, setDescription] = useState(initialValues?.description ?? "");
  const [icon, setIcon] = useState(initialValues?.icon ?? "signal");
  const [sourceCode, setSourceCode] = useState(initialValues?.sourceCode ?? "");
  const [submitting, setSubmitting] = useState(false);
  // null | {type: "generic", message} | {type: "syntax", message, line, sourceLine}
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  async function handleSubmit(event) {
    event.preventDefault();
    setError(null);
    setResult(null);

    if (!sourceCode.trim()) {
      setError({ type: "generic", message: "Macro source can't be empty." });
      return;
    }

    setSubmitting(true);
    try {
      const built = await callProtected(() =>
        buildMacro(token, technicalName, { displayName, description, icon, sourceCode })
      );
      setResult(built);
      onBuilt();
    } catch (err) {
      if (err instanceof MacroSyntaxError) {
        setError({ type: "syntax", message: err.message, line: err.line, sourceLine: err.sourceLine });
      } else if (err instanceof BuildFailedError) {
        setError({ type: "generic", message: err.message });
      } else {
        setError({ type: "generic", message: err.message || "build failed" });
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="p-6">
      {(title || onCancel) && (
        <div className="mb-5 flex items-center justify-between">
          {title && <h2 className="text-sm font-medium text-signal-100">{title}</h2>}
          {onCancel && (
            <Button variant="ghost" size="icon" onClick={onCancel} aria-label="Close">
              <X className="h-4 w-4" strokeWidth={1.75} />
            </Button>
          )}
        </div>
      )}

      <form onSubmit={handleSubmit}>
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label
                htmlFor="technical-name"
                className="mb-1.5 block text-xs font-medium text-signal-400"
              >
                Technical name
              </label>
              <input
                id="technical-name"
                type="text"
                value={technicalName}
                onChange={(e) => setTechnicalName(e.target.value)}
                placeholder="e.g. rtwp-anomaly-demo-v2"
                required
                disabled={!technicalNameEditable}
                className="w-full rounded-lg border border-signal-600 bg-signal-800 px-3 py-2 font-mono text-sm text-signal-100 placeholder-signal-400 outline-none transition-colors focus:border-amber-500 focus:ring-1 focus:ring-amber-500 disabled:cursor-not-allowed disabled:text-signal-400 disabled:opacity-60"
              />
            </div>

            <div>
              <label
                htmlFor="display-name"
                className="mb-1.5 block text-xs font-medium text-signal-400"
              >
                Display name
              </label>
              <input
                id="display-name"
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="e.g. RTWP Anomaly Detector v2"
                required
                className="w-full rounded-lg border border-signal-600 bg-signal-800 px-3 py-2 text-sm text-signal-100 placeholder-signal-400 outline-none transition-colors focus:border-amber-500 focus:ring-1 focus:ring-amber-500"
              />
            </div>
          </div>

          <div>
            <label
              htmlFor="description"
              className="mb-1.5 block text-xs font-medium text-signal-400"
            >
              Description
            </label>
            <input
              id="description"
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What does this macro do?"
              className="w-full rounded-lg border border-signal-600 bg-signal-800 px-3 py-2 text-sm text-signal-100 placeholder-signal-400 outline-none transition-colors focus:border-amber-500 focus:ring-1 focus:ring-amber-500"
            />
          </div>

          <IconPicker value={icon} onChange={setIcon} />

          <div>
            <label
              htmlFor="macro-source"
              className="mb-1.5 block text-xs font-medium text-signal-400"
            >
              Macro source (Python)
            </label>
            <textarea
              id="macro-source"
              value={sourceCode}
              onChange={(e) => setSourceCode(e.target.value)}
              placeholder="import pandas as pd&#10;&#10;# reads INPUT_PATH, writes OUTPUT_PATH"
              rows={14}
              className="w-full rounded-lg border border-signal-600 bg-signal-800 px-3 py-2 font-mono text-xs leading-relaxed text-signal-100 placeholder-signal-400 outline-none transition-colors focus:border-amber-500 focus:ring-1 focus:ring-amber-500"
            />
          </div>
        </div>

        <BuildError error={error} />

        <Button type="submit" disabled={submitting} className="mt-4">
          {submitting ? submittingLabel : submitLabel}
        </Button>
      </form>

      {result && <BuildResult result={result} />}
    </div>
  );
}

// Branches on error.type: "syntax" gets its own panel with the line
// number and a one-line source snippet (main.py's MacroSyntaxError
// handler / api/client.js's MacroSyntaxError carry exactly these three
// fields); anything else ("generic" -- empty source, a failed image
// build, an unexpected error) gets the existing plain message panel.
// Both replace whatever ad hoc error text used to render here.
function BuildError({ error }) {
  if (!error) return null;

  if (error.type === "syntax") {
    return (
      <div
        role="alert"
        className="mt-4 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2.5 text-sm text-danger"
      >
        <div className="flex items-start gap-2">
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={1.75} />
          <div className="min-w-0 flex-1">
            <p className="font-medium">
              Syntax error{error.line != null ? ` on line ${error.line}` : ""}
            </p>
            <p className="mt-0.5 text-danger/90">{error.message}</p>
          </div>
        </div>
        {error.sourceLine && (
          <pre className="mt-2 overflow-x-auto rounded-md bg-danger/15 px-2.5 py-1.5 font-mono text-xs text-signal-100">
            <code>{error.sourceLine}</code>
          </pre>
        )}
      </div>
    );
  }

  return (
    <div
      role="alert"
      className="mt-4 flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger"
    >
      {error.message}
    </div>
  );
}

function BuildResult({ result }) {
  return (
    <div className="mt-6 border-t border-signal-700 pt-6">
      <div className="mb-3 flex items-center gap-2">
        <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4 text-amber-500" aria-hidden="true">
          <path
            d="M4 10.5l4 4 8-9"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        <p className="font-mono text-sm text-signal-100">{result.image_tag}</p>
      </div>

      <dl className="mb-4 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
        <Stat label="output type" value={result.output_type} />
        <Stat label="imports" value={result.imports.join(", ") || "—"} />
        <Stat
          label="required columns"
          value={result.required_columns.join(", ") || "—"}
          className="col-span-2"
        />
      </dl>

      <div className="space-y-2">
        {Object.entries(result.artifacts).map(([filename, content]) => (
          <ArtifactBlock key={filename} filename={filename} content={content} />
        ))}
      </div>
    </div>
  );
}

function Stat({ label, value, className = "" }) {
  return (
    <div className={className}>
      <dt className="text-signal-400">{label}</dt>
      <dd className="mt-0.5 truncate font-mono text-signal-200">{value}</dd>
    </div>
  );
}

function ArtifactBlock({ filename, content }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="overflow-hidden rounded-lg border border-signal-700">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between bg-signal-800 px-3 py-2 text-left text-xs font-medium text-signal-200 transition-colors hover:bg-signal-700"
      >
        <span className="font-mono">{filename}</span>
        <span className="text-signal-400">{open ? "hide" : "show"}</span>
      </button>
      {open && (
        <pre className="overflow-x-auto bg-signal-950 px-3 py-3 text-xs leading-relaxed text-signal-200">
          <code>{content}</code>
        </pre>
      )}
    </div>
  );
}
