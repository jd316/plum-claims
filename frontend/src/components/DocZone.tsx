import { useEffect, useRef, useState } from "react";

import { classifyDocument } from "../api";
import { DOC_TYPE_LABELS } from "../labels";
import type { ClassifyResult, DocType } from "../types";

const ACCEPT = "image/png,image/jpeg,application/pdf";

function isAccepted(file: File): boolean {
  return (
    file.type === "image/png" ||
    file.type === "image/jpeg" ||
    file.type === "application/pdf"
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Live shift-left status for a single required-document slot. */
export type ZoneStatus =
  | { kind: "idle" }
  | { kind: "checking" }
  | { kind: "ok"; docType: DocType }
  | { kind: "wrong"; detected: DocType }
  | { kind: "unreadable"; quality?: string[] }
  | { kind: "unknown" };

interface DocZoneProps {
  /** The document type this slot expects, e.g. "PRESCRIPTION". */
  expected: DocType;
  file: File | null;
  onChange: (file: File | null) => void;
  /** Bubbled up so the page can surface a soft warning summary if needed. */
  onStatus?: (status: ZoneStatus) => void;
}

/** A labelled single-file drop-zone that classifies the dropped file live and
 *  shows shift-left feedback (right doc? legible?) BEFORE the member submits.
 *  These are soft, helpful warnings — they never block submission. */
export default function DocZone({
  expected,
  file,
  onChange,
  onStatus,
}: DocZoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [status, setStatus] = useState<ZoneStatus>({ kind: "idle" });
  // Guards against a stale classify response overwriting a newer file's status.
  const runId = useRef(0);

  useEffect(() => {
    onStatus?.(status);
  }, [status, onStatus]);

  function classify(f: File) {
    const myRun = ++runId.current;
    setStatus({ kind: "checking" });
    classifyDocument(f)
      .then((res: ClassifyResult) => {
        if (myRun !== runId.current) return; // superseded by a newer file
        if (res.error || res.doc_type === "UNKNOWN") {
          setStatus({ kind: "unknown" });
        } else if (res.readable === false) {
          setStatus({ kind: "unreadable", quality: res.quality_issues });
        } else if (res.doc_type === expected) {
          setStatus({ kind: "ok", docType: res.doc_type });
        } else {
          setStatus({ kind: "wrong", detected: res.doc_type });
        }
      })
      .catch(() => {
        if (myRun !== runId.current) return;
        setStatus({ kind: "unknown" });
      });
  }

  function setFile(f: File | null) {
    runId.current++; // invalidate any in-flight classify
    onChange(f);
    if (f) classify(f);
    else setStatus({ kind: "idle" });
  }

  function handleIncoming(list: FileList | null) {
    if (!list || list.length === 0) return;
    const f = Array.from(list).find(isAccepted);
    if (f) setFile(f);
  }

  const label = DOC_TYPE_LABELS[expected];

  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm font-medium text-plum-800 dark:text-creamtext">Upload {label}</p>

      {file ? (
        <div className="flex items-center gap-3 rounded-xl border border-plum-800/10 dark:border-creamtext/10 bg-white dark:bg-plum-800 px-3 py-2.5">
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-plum-800 dark:text-creamtext">
              {file.name}
            </p>
            <p className="text-xs text-plum-800/50 dark:text-creamtext/50">{formatSize(file.size)}</p>
          </div>
          <button
            type="button"
            onClick={() => setFile(null)}
            aria-label={`Remove ${file.name}`}
            className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full text-plum-800/50 dark:text-creamtext/50 transition-colors hover:bg-crimson/10 hover:text-crimson"
          >
            <span aria-hidden className="text-lg leading-none">
              &times;
            </span>
          </button>
        </div>
      ) : (
        <div
          role="button"
          tabIndex={0}
          aria-label={`Upload ${label} — click or drag and drop a PNG, JPG, or PDF`}
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              inputRef.current?.click();
            }
          }}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={(e) => {
            e.preventDefault();
            if (!e.currentTarget.contains(e.relatedTarget as Node)) {
              setDragging(false);
            }
          }}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            handleIncoming(e.dataTransfer.files);
          }}
          className={[
            "flex cursor-pointer items-center justify-center gap-2 rounded-xl border-2 border-dashed px-4 py-5 text-center text-sm transition-colors",
            dragging
              ? "border-coral bg-coral/5 dark:bg-creamtext/5"
              : "border-plum-800/20 dark:border-creamtext/20 bg-plum-800/[0.02] dark:bg-creamtext/5 hover:border-coral/60 hover:bg-coral/[0.03]",
          ].join(" ")}
        >
          <span className="font-medium text-plum-800 dark:text-creamtext">
            <span className="text-coral">Click to upload</span> or drag &amp; drop
          </span>
          <span className="text-xs text-plum-800/40 dark:text-creamtext/40">PNG, JPG or PDF</span>
        </div>
      )}

      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        aria-label={`Upload ${expected} document`}
        className="hidden"
        onChange={(e) => {
          handleIncoming(e.target.files);
          e.target.value = "";
        }}
      />

      {/* Live region so the async classification result (checking → ok / wrong type /
          unreadable) is announced to screen readers as it changes, not just shown. */}
      <div aria-live="polite" aria-atomic="true">
        <ZoneFeedback expected={expected} status={status} />
      </div>
    </div>
  );
}

function ZoneFeedback({
  expected,
  status,
}: {
  expected: DocType;
  status: ZoneStatus;
}) {
  const expectedLabel = DOC_TYPE_LABELS[expected].toLowerCase();

  if (status.kind === "checking") {
    return (
      <p className="flex items-center gap-2 text-xs font-medium text-plum-800/55 dark:text-creamtext/55">
        <svg className="h-3.5 w-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
          <circle
            className="opacity-25"
            cx="12"
            cy="12"
            r="10"
            stroke="currentColor"
            strokeWidth="4"
          />
          <path
            className="opacity-90"
            fill="currentColor"
            d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4Z"
          />
        </svg>
        Checking…
      </p>
    );
  }

  if (status.kind === "ok") {
    return (
      <p className="flex items-center gap-1.5 rounded-lg bg-growth/10 px-2.5 py-1.5 text-xs font-medium text-growth">
        <span aria-hidden>✓</span>
        Looks like a valid {DOC_TYPE_LABELS[status.docType].toLowerCase()}.
      </p>
    );
  }

  if (status.kind === "wrong") {
    return (
      <p className="rounded-lg border border-molten/30 bg-molten/[0.08] px-2.5 py-1.5 text-xs font-medium text-molten">
        This looks like a {DOC_TYPE_LABELS[status.detected].toLowerCase()}, but
        this slot needs a {expectedLabel}. Please replace this file.
      </p>
    );
  }

  if (status.kind === "unreadable") {
    return (
      <p className="rounded-lg border border-molten/30 bg-molten/[0.08] px-2.5 py-1.5 text-xs font-medium text-molten">
        This {expectedLabel} is too blurry/unreadable — please retake and
        re-upload.
      </p>
    );
  }

  if (status.kind === "unknown") {
    return (
      <p className="rounded-lg bg-plum-800/[0.04] dark:bg-creamtext/10 px-2.5 py-1.5 text-xs text-plum-800/55 dark:text-creamtext/55">
        Couldn&apos;t auto-check this file — you can still submit; we&apos;ll
        verify it on processing.
      </p>
    );
  }

  return null;
}
