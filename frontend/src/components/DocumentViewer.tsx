import { useEffect, useMemo, useState } from "react";

import { authHeader, documentFileUrl, getClaimDocuments } from "../api";
import { DOC_TYPE_LABELS } from "../labels";
import type { ClaimDocument } from "../types";

const ZOOM_MIN = 1;
const ZOOM_MAX = 4;
const ZOOM_STEP = 0.5;

function docLabel(doc: ClaimDocument): string {
  return DOC_TYPE_LABELS[doc.doc_type] ?? "Document";
}

function isPdf(doc: ClaimDocument): boolean {
  return doc.content_type === "application/pdf";
}

/** A single document's viewing area: PDF in an embed, image with zoom controls. */
function DocumentStage({
  claimId,
  doc,
}: {
  claimId: string;
  doc: ClaimDocument;
}) {
  const [zoom, setZoom] = useState(1);
  const [rotation, setRotation] = useState(0); // degrees: 0 | 90 | 180 | 270
  const [errored, setErrored] = useState(false);
  // Load the document WITH the auth header and view it via an object URL. A raw
  // <img>/<embed> src can't carry the JWT bearer token, so pointing it straight at the
  // API 401s when auth is on. Fetch → blob → object URL works in both auth modes.
  const [src, setSrc] = useState<string | null>(null);
  const url = documentFileUrl(claimId, doc.file_id);
  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    fetch(url, { headers: authHeader() })
      .then((r) => {
        if (!r.ok) throw new Error(String(r.status));
        return r.blob();
      })
      .then((blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch(() => active && setErrored(true));
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url]);

  // Transient view state (zoom/rotation/errored) resets automatically when the
  // selected document changes: the parent renders this with key={doc.file_id},
  // so React remounts it fresh — no reset effect needed.

  if (errored) {
    return (
      <div className="flex h-full min-h-[20rem] flex-col items-center justify-center gap-2 p-8 text-center">
        <svg
          className="h-8 w-8 text-plum-800/30 dark:text-creamtext/30"
          fill="none"
          viewBox="0 0 24 24"
          strokeWidth={1.6}
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5"
          />
        </svg>
        <p className="text-sm font-medium text-plum-800/60 dark:text-creamtext/60">
          This file isn't available
        </p>
        <p className="text-xs text-plum-800/40 dark:text-creamtext/40">
          The source file may have been removed from storage.
        </p>
      </div>
    );
  }

  if (!src) {
    return (
      <div className="flex h-full min-h-[20rem] items-center justify-center p-8">
        <svg className="h-6 w-6 animate-spin text-plum-800/40 dark:text-creamtext/40" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-90" fill="currentColor" d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4Z" />
        </svg>
        <span className="sr-only">Loading document…</span>
      </div>
    );
  }

  if (isPdf(doc)) {
    // The box height is viewport-relative so it scales with the screen instead of
    // locking to a short fixed height (the panel sits beside a much taller decision
    // column). `#view=FitH` tells the browser's PDF viewer to scale the page to the
    // panel width, so the document fills the box rather than rendering tiny/centered.
    return (
      <embed
        src={`${src}#view=FitH`}
        type="application/pdf"
        className="block h-[78vh] max-h-[60rem] min-h-[28rem] w-full"
        onError={() => setErrored(true)}
      />
    );
  }

  return (
    <div className="relative flex h-[78vh] max-h-[60rem] min-h-[28rem] flex-col">
      <div className="flex-1 overflow-auto bg-plum-800/[0.03] dark:bg-creamtext/5 p-4">
        <img
          src={src}
          alt={doc.file_name ?? docLabel(doc)}
          onClick={() =>
            setZoom((z) => (z >= ZOOM_MAX ? ZOOM_MIN : z + ZOOM_STEP))
          }
          onError={() => setErrored(true)}
          style={{ width: `${zoom * 100}%`, transform: `rotate(${rotation}deg)` }}
          className="mx-auto h-auto max-w-none cursor-zoom-in rounded-md shadow-sm transition-[width,transform] duration-150"
        />
      </div>
      <div className="flex items-center justify-end gap-1.5 border-t border-plum-800/[0.07] dark:border-creamtext/10 bg-white/60 dark:bg-plum-700 px-3 py-2">
        <button
          type="button"
          onClick={() => setRotation((r) => (r + 270) % 360)}
          aria-label="Rotate left 90 degrees"
          className="flex h-7 w-7 items-center justify-center rounded-full border border-plum-800/15 dark:border-creamtext/15 text-plum-800/70 dark:text-creamtext/70 transition-colors hover:bg-plum-800/5 dark:hover:bg-creamtext/10"
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.8} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 15 4.5 10.5 9 6" />
            <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 10.5h9a6 6 0 0 1 6 6v1.5" />
          </svg>
        </button>
        <button
          type="button"
          onClick={() => setRotation((r) => (r + 90) % 360)}
          aria-label="Rotate right 90 degrees"
          className="flex h-7 w-7 items-center justify-center rounded-full border border-plum-800/15 dark:border-creamtext/15 text-plum-800/70 dark:text-creamtext/70 transition-colors hover:bg-plum-800/5 dark:hover:bg-creamtext/10"
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.8} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="m15 15 4.5-4.5L15 6" />
            <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 10.5h-9a6 6 0 0 0-6 6v1.5" />
          </svg>
        </button>
        <span className="mx-1 h-4 w-px bg-plum-800/15 dark:bg-creamtext/15" aria-hidden />
        <button
          type="button"
          onClick={() => setZoom((z) => Math.max(ZOOM_MIN, z - ZOOM_STEP))}
          disabled={zoom <= ZOOM_MIN}
          aria-label="Zoom out"
          className="flex h-7 w-7 items-center justify-center rounded-full border border-plum-800/15 dark:border-creamtext/15 text-plum-800/70 dark:text-creamtext/70 transition-colors hover:bg-plum-800/5 dark:hover:bg-creamtext/10 disabled:opacity-30"
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 12h14" />
          </svg>
        </button>
        <span className="w-12 text-center font-mono text-xs text-plum-800/55 dark:text-creamtext/55">
          {Math.round(zoom * 100)}%
        </span>
        <button
          type="button"
          onClick={() => setZoom((z) => Math.min(ZOOM_MAX, z + ZOOM_STEP))}
          disabled={zoom >= ZOOM_MAX}
          aria-label="Zoom in"
          className="flex h-7 w-7 items-center justify-center rounded-full border border-plum-800/15 dark:border-creamtext/15 text-plum-800/70 dark:text-creamtext/70 transition-colors hover:bg-plum-800/5 dark:hover:bg-creamtext/10 disabled:opacity-30"
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 5v14M5 12h14" />
          </svg>
        </button>
      </div>
    </div>
  );
}

/**
 * Left-pane Ops document viewer. Fetches the claim's source documents and shows
 * a vertical tab strip plus a viewing stage. Renders `null` when there are no
 * documents (or the fetch fails) so Claim.tsx can fall back to a single column.
 */
export default function DocumentViewer({
  claimId,
  onAvailabilityChange,
}: {
  claimId: string;
  onAvailabilityChange?: (hasDocs: boolean) => void;
}) {
  const [docs, setDocs] = useState<ClaimDocument[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getClaimDocuments(claimId)
      .then((d) => {
        if (!active) return;
        setDocs(d);
        setActiveId(d[0]?.file_id ?? null);
        onAvailabilityChange?.(d.length > 0);
      })
      .catch(() => {
        if (!active) return;
        setFailed(true);
        onAvailabilityChange?.(false);
      });
    return () => {
      active = false;
    };
  }, [claimId, onAvailabilityChange]);

  const activeDoc = useMemo(
    () => docs?.find((d) => d.file_id === activeId) ?? null,
    [docs, activeId]
  );

  // No documents (older claims) or a failed fetch → render nothing.
  if (failed || (docs && docs.length === 0)) return null;
  if (!docs || !activeDoc) return null;

  return (
    <section className="overflow-hidden rounded-card border border-plum-800/[0.08] dark:border-creamtext/10 bg-white dark:bg-plum-800 shadow-sm">
      <div className="flex items-center justify-between border-b border-plum-800/[0.08] dark:border-creamtext/10 px-5 py-3.5">
        <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-plum-800/45 dark:text-creamtext/45">
          Source documents
        </p>
        <span className="font-mono text-[11px] text-plum-800/35 dark:text-creamtext/35">
          {docs.length} {docs.length === 1 ? "file" : "files"}
        </span>
      </div>

      <div className="flex flex-col sm:flex-row">
        {docs.length > 1 && (
          <nav className="flex w-full flex-shrink-0 flex-row gap-1 overflow-x-auto border-b border-plum-800/[0.07] bg-cream/40 p-2.5 dark:border-creamtext/10 dark:bg-plum-900 sm:w-36 sm:flex-col sm:border-b-0 sm:border-r">
            {docs.map((d) => {
              const isActive = d.file_id === activeId;
              return (
                <button
                  key={d.file_id}
                  type="button"
                  onClick={() => setActiveId(d.file_id)}
                  className={`flex w-32 flex-shrink-0 flex-col gap-0.5 rounded-lg px-3 py-2.5 text-left transition-colors sm:w-auto ${
                    isActive
                      ? "bg-coral text-white shadow-sm"
                      : "text-plum-800/70 dark:text-creamtext/70 hover:bg-plum-800/5 dark:hover:bg-creamtext/10"
                  }`}
                >
                  <span className="text-xs font-semibold leading-tight">
                    {docLabel(d)}
                  </span>
                  <span
                    className={`truncate font-mono text-[10px] ${
                      isActive ? "text-white/70" : "text-plum-800/40 dark:text-creamtext/40"
                    }`}
                    title={d.file_name ?? d.file_id}
                  >
                    {d.file_name ?? d.file_id}
                  </span>
                </button>
              );
            })}
          </nav>
        )}

        <div className="min-w-0 flex-1">
          {docs.length === 1 && (
            <div className="border-b border-plum-800/[0.06] dark:border-creamtext/10 px-5 py-2.5">
              <p className="text-xs font-semibold text-plum-800/75 dark:text-creamtext/75">
                {docLabel(activeDoc)}
              </p>
              <p className="truncate font-mono text-[10px] text-plum-800/40 dark:text-creamtext/40">
                {activeDoc.file_name ?? activeDoc.file_id}
              </p>
            </div>
          )}
          <DocumentStage key={activeDoc.file_id} claimId={claimId} doc={activeDoc} />
        </div>
      </div>
    </section>
  );
}
