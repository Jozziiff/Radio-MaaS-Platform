// RunPanel (M6): expand-in-place execution screen, same pattern as
// MacroForm's create/edit panel -- run a macro against an uploaded CSV,
// watch its status, download the result.
//
// Pre-execution validation (M6, continued): selecting a file immediately
// calls uploadInput, which now validates the CSV's header against the
// macro's required columns *before* storing it (see main.py's
// upload_macro_input and docs/decisions/ for the backend side). There is
// no separate "validate" vs. "upload" step -- a passing validation IS the
// upload, since the backend only writes to MinIO once the header check
// passes. "Run" stays disabled until that call succeeds for the file
// currently selected; picking a different file resets validation and
// disables Run again until the new file's own upload/validate call
// resolves.
//
// State machine: idle -> validating -> validated | invalid -> running ->
// succeeded | failed. "running" polls GET /executions/{job_name} every 2s
// via a setInterval cleaned up by useEffect's own return function -- that
// cleanup fires on every dependency change AND on unmount, so closing the
// panel mid-run (unmounting this component) or reaching a terminal status
// (status state changes, effect re-runs, old interval is cleared first)
// both stop polling. There is never a moment where two intervals are both
// alive.

import { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import {
  Loader2,
  UploadCloud,
  Download,
  X,
  CircleAlert,
  CircleCheck,
  ListX,
} from "lucide-react";
import {
  uploadInput,
  runMacro,
  getExecutionStatus,
  downloadResult,
  ValidationError,
} from "../api/client";
import Button from "../components/Button";

const POLL_INTERVAL_MS = 2000;

export default function RunPanel({ token, callProtected, macro, onClose }) {
  const [file, setFile] = useState(null);
  // null | "validating" | {ok: true, matchedColumns} | {ok: false, missingColumns, detectedHeaders} | {ok: false, message}
  const [validation, setValidation] = useState(null);

  const [phase, setPhase] = useState("idle"); // idle | starting | running | succeeded | failed
  const [status, setStatus] = useState(null); // raw backend status: pending | running | succeeded | failed
  const [jobName, setJobName] = useState(null);
  const [error, setError] = useState(null);
  const [downloading, setDownloading] = useState(false);

  const fileInputRef = useRef(null);

  useEffect(() => {
    if (phase !== "running" || !jobName) return;

    const interval = setInterval(async () => {
      try {
        const result = await callProtected(() => getExecutionStatus(token, jobName));
        setStatus(result.status);
        if (result.status === "succeeded" || result.status === "failed") {
          setPhase(result.status);
        }
      } catch (err) {
        setError(err.message || "lost track of the execution");
        setPhase("failed");
      }
    }, POLL_INTERVAL_MS);

    return () => clearInterval(interval);
  }, [phase, jobName, token, callProtected]);

  async function handleFileSelected(selected) {
    setFile(selected);
    setError(null);
    if (!selected) {
      setValidation(null);
      return;
    }

    setValidation("validating");
    try {
      const result = await callProtected(() => uploadInput(token, macro.technical_name, selected));
      setValidation({ ok: true, matchedColumns: result.matched_columns });
    } catch (err) {
      if (err instanceof ValidationError) {
        setValidation({
          ok: false,
          missingColumns: err.missingColumns,
          detectedHeaders: err.detectedHeaders,
        });
      } else {
        setValidation({ ok: false, message: err.message || "could not validate this file" });
      }
    }
  }

  async function handleRun() {
    if (validation?.ok !== true) return;
    setError(null);
    setPhase("starting");
    try {
      const created = await callProtected(() => runMacro(token, macro.technical_name));
      setJobName(created.job_name);
      setStatus("pending");
      setPhase("running");
    } catch (err) {
      setError(err.message || "failed to start the execution");
      setPhase("failed");
    }
  }

  async function handleDownload() {
    setDownloading(true);
    setError(null);
    try {
      const blob = await callProtected(() => downloadResult(token, jobName));
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${macro.technical_name}-result.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err.message || "failed to download the result");
    } finally {
      setDownloading(false);
    }
  }

  function reset() {
    setFile(null);
    setValidation(null);
    setPhase("idle");
    setStatus(null);
    setJobName(null);
    setError(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  return (
    <div className="p-6">
      <div className="mb-5 flex items-center justify-between">
        <h2 className="text-sm font-medium text-signal-100">Run {macro.display_name}</h2>
        <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close">
          <X className="h-4 w-4" strokeWidth={1.75} />
        </Button>
      </div>

      {(phase === "idle" || phase === "starting") && (
        <div className="space-y-4">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-signal-400">
              Input CSV
            </label>
            <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-dashed border-signal-600 bg-signal-800 px-4 py-3 text-sm text-signal-400 transition-colors hover:border-amber-500/50 hover:text-signal-200">
              <UploadCloud className="h-4 w-4 shrink-0" strokeWidth={1.75} />
              <span className="truncate">{file ? file.name : "Choose a CSV file…"}</span>
              <input
                ref={fileInputRef}
                type="file"
                accept=".csv,text/csv"
                onChange={(e) => handleFileSelected(e.target.files?.[0] ?? null)}
                className="hidden"
              />
            </label>

            <ValidationResult validation={validation} />
          </div>

          <Button onClick={handleRun} disabled={validation?.ok !== true || phase === "starting"}>
            {phase === "starting" && <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2} />}
            {phase === "starting" ? "Starting…" : "Run"}
          </Button>
        </div>
      )}

      {phase === "running" && (
        <div className="flex items-center gap-4 rounded-lg border border-signal-700 bg-signal-800 px-4 py-5">
          <SpinningRing />
          <div>
            <p className="text-sm font-medium text-signal-100">Execution in progress</p>
            <p className="mt-0.5 font-mono text-xs text-signal-400">
              {jobName} &middot; {status}
            </p>
          </div>
        </div>
      )}

      {phase === "succeeded" && (
        <div className="flex items-center justify-between gap-4 rounded-lg border border-success-500/30 bg-success-500/10 px-4 py-4">
          <div className="flex items-center gap-3">
            <CircleCheck className="h-5 w-5 shrink-0 text-success-400" strokeWidth={1.75} />
            <div>
              <p className="text-sm font-medium text-signal-100">Execution succeeded</p>
              <p className="mt-0.5 font-mono text-xs text-signal-400">{jobName}</p>
            </div>
          </div>
          <Button onClick={handleDownload} disabled={downloading} size="compact">
            {downloading ? (
              <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2} />
            ) : (
              <Download className="h-4 w-4" strokeWidth={2} />
            )}
            Download result
          </Button>
        </div>
      )}

      {phase === "failed" && (
        <div className="rounded-lg border border-danger/30 bg-danger/10 px-4 py-4">
          <div className="flex items-start gap-3">
            <CircleAlert className="mt-0.5 h-5 w-5 shrink-0 text-danger" strokeWidth={1.75} />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-signal-100">Execution failed</p>
              <p className="mt-1 text-sm text-danger">
                {error || "The execution did not complete successfully."}
              </p>
              {jobName && <p className="mt-1 font-mono text-xs text-signal-400">{jobName}</p>}
            </div>
          </div>
          <Button variant="secondary" size="sm" onClick={reset} className="mt-3">
            Try again
          </Button>
        </div>
      )}
    </div>
  );
}

function ValidationResult({ validation }) {
  if (!validation) return null;

  if (validation === "validating") {
    return (
      <div className="mt-2 flex items-center gap-2 text-xs text-signal-400">
        <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} />
        Checking columns…
      </div>
    );
  }

  if (validation.ok) {
    return (
      <div className="mt-2 flex items-start gap-2 rounded-lg border border-success-500/30 bg-success-500/10 px-3 py-2 text-xs text-success-400">
        <CircleCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.75} />
        <span>
          Looks good — matched{" "}
          <span className="font-mono text-success-400">
            {validation.matchedColumns.length > 0 ? validation.matchedColumns.join(", ") : "no required columns"}
          </span>
          .
        </span>
      </div>
    );
  }

  if (validation.missingColumns) {
    return (
      <div className="mt-2 flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
        <ListX className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.75} />
        <span>
          Missing required column{validation.missingColumns.length > 1 ? "s" : ""}:{" "}
          <span className="font-mono">{validation.missingColumns.join(", ")}</span>
          {validation.detectedHeaders && validation.detectedHeaders.length > 0 && (
            <>
              {" "}
              (found: <span className="font-mono">{validation.detectedHeaders.join(", ")}</span>)
            </>
          )}
        </span>
      </div>
    );
  }

  return (
    <div className="mt-2 flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
      <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.75} />
      <span>{validation.message}</span>
    </div>
  );
}

function SpinningRing() {
  return (
    <motion.div
      animate={{ rotate: 360 }}
      transition={{ duration: 1, repeat: Infinity, ease: "linear" }}
      className="h-8 w-8 shrink-0 rounded-full border-2 border-signal-600 border-t-amber-500"
    />
  );
}
