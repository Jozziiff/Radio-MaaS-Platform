// History page (M6, continued): reads GET /executions (db.py's executions
// table, see docs/decisions/006-execution-history.md) and shows every
// recorded run, newest first -- the endpoint already returns that order,
// so this never re-sorts.
//
// Auto-refresh discipline mirrors RunPanel.jsx exactly: a useEffect-owned
// setInterval, cleaned up on every dependency change and on unmount, so
// there's never a dangling poll. The one difference from RunPanel is the
// stop condition -- RunPanel polls a single job until it's terminal; this
// polls the whole list and stops the moment every row is terminal
// (nothing pending/running left to watch), restarting automatically if a
// fresh GET /executions ever finds a new non-terminal row (e.g. the user
// started a run from the catalog and switched back to History).

import { useEffect, useState } from "react";
import { Download, Loader2, ListX } from "lucide-react";
import { useAuth } from "../auth/AuthContext";
import { useProtectedApi } from "../auth/useProtectedApi";
import { listExecutions, downloadResult } from "../api/client";
import Shell from "../components/Shell";
import TopBar from "../components/TopBar";
import Button from "../components/Button";
import Card from "../components/Card";
import Badge from "../components/Badge";
import Skeleton from "../components/Skeleton";

const POLL_INTERVAL_MS = 5000;
const TERMINAL_STATUSES = new Set(["succeeded", "failed"]);

export default function HistoryPage({ page, onNavigate }) {
  const { session } = useAuth();
  const callProtected = useProtectedApi();

  const [executions, setExecutions] = useState(null); // null = still loading
  const [error, setError] = useState(null);

  async function refresh() {
    try {
      const result = await callProtected(() => listExecutions(session.token));
      setExecutions(result);
      setError(null);
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const hasUnfinished = executions?.some((e) => !TERMINAL_STATUSES.has(e.status)) ?? false;

  useEffect(() => {
    if (!hasUnfinished) return;

    const interval = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasUnfinished]);

  return (
    <Shell page={page} onNavigate={onNavigate}>
      <TopBar
        title="Execution history"
        description="Every run recorded so far, independent of whether its Kubernetes Job still exists."
      />

      <main className="mx-auto max-w-4xl px-8 pb-10">
        <HistoryTable executions={executions} error={error} token={session.token} callProtected={callProtected} />
      </main>
    </Shell>
  );
}

function HistoryTable({ executions, error, token, callProtected }) {
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

  if (executions === null) {
    return <Skeleton count={3} height="h-12" />;
  }

  if (executions.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-signal-700 px-6 py-12 text-center">
        <p className="text-sm text-signal-400">No executions yet — run a macro to see it here.</p>
      </div>
    );
  }

  return (
    <Card className="overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-signal-700 text-xs text-signal-400">
              <th className="px-4 py-2.5 font-medium">Macro</th>
              <th className="px-4 py-2.5 font-medium">Status</th>
              <th className="px-4 py-2.5 font-medium">Started</th>
              <th className="px-4 py-2.5 font-medium">Duration</th>
              <th className="px-4 py-2.5 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {executions.map((execution) => (
              <ExecutionRow
                key={execution.job_name}
                execution={execution}
                token={token}
                callProtected={callProtected}
              />
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function ExecutionRow({ execution, token, callProtected }) {
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState(null);

  async function handleDownload() {
    setDownloading(true);
    setDownloadError(null);
    try {
      const blob = await callProtected(() => downloadResult(token, execution.job_name));
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${execution.macro_name}-result.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setDownloadError(err.message || "failed to download the result");
    } finally {
      setDownloading(false);
    }
  }

  return (
    <tr className="border-b border-signal-700 last:border-0 hover:bg-signal-800/60">
      <td className="px-4 py-3">
        <p className="font-medium text-signal-100">{execution.macro_name}</p>
        <p className="font-mono text-xs text-signal-400">{execution.job_name}</p>
        {execution.run_by && <p className="mt-0.5 text-xs text-signal-400">by {execution.run_by}</p>}
      </td>
      <td className="px-4 py-3">
        <Badge status={execution.status} />
      </td>
      <td className="px-4 py-3 text-signal-400">{formatCreatedAt(execution.created_at)}</td>
      <td className="px-4 py-3 text-signal-400">
        {formatDuration(execution.created_at, execution.finished_at)}
      </td>
      <td className="px-4 py-3 text-right">
        {execution.status === "succeeded" && (
          <div className="flex flex-col items-end gap-1">
            <Button variant="chip" size="sm" onClick={handleDownload} disabled={downloading}>
              {downloading ? (
                <Loader2 className="h-3 w-3 animate-spin" strokeWidth={2} />
              ) : (
                <Download className="h-3 w-3" strokeWidth={2} />
              )}
              Download
            </Button>
            {downloadError && (
              <span className="flex items-center gap-1 text-xs text-danger">
                <ListX className="h-3 w-3" strokeWidth={1.75} />
                {downloadError}
              </span>
            )}
          </div>
        )}
      </td>
    </tr>
  );
}

function formatCreatedAt(isoString) {
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return isoString;
  return date.toLocaleString();
}

// Renders like "12s" or "1m 4s" -- omits the minutes part entirely under a
// minute, rather than always showing "0m 12s".
function formatDuration(createdAt, finishedAt) {
  if (!finishedAt) return "—";

  const start = new Date(createdAt);
  const end = new Date(finishedAt);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return "—";

  const totalSeconds = Math.max(0, Math.round((end - start) / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;

  return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;
}
