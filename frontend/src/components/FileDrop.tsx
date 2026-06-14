import { useEffect, useMemo, useRef, useState } from "react";

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

/** Thumbnail for an image file; creates and revokes an object URL on its own. */
function Thumb({ file }: { file: File }) {
  // Derive the object URL during render (no setState-in-effect); revoke it on
  // unmount / when it changes via a cleanup-only effect.
  const url = useMemo(
    () => (file.type.startsWith("image/") ? URL.createObjectURL(file) : null),
    [file],
  );
  useEffect(() => {
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [url]);

  if (url) {
    return (
      <img
        src={url}
        alt={file.name}
        className="h-12 w-12 flex-shrink-0 rounded-lg object-cover ring-1 ring-plum-800/10 dark:ring-creamtext/10"
      />
    );
  }
  // PDF / non-image fallback tile
  return (
    <div className="flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-lg bg-plum-800/5 dark:bg-creamtext/10 text-[10px] font-semibold uppercase tracking-wide text-plum-800/60 dark:text-creamtext/60 ring-1 ring-plum-800/10 dark:ring-creamtext/10">
      PDF
    </div>
  );
}

interface FileDropProps {
  files: File[];
  onChange: (files: File[]) => void;
  /** Optional id so an external <label> can target the input. */
  id?: string;
}

export default function FileDrop({ files, onChange, id }: FileDropProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const cameraRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [skippedMessage, setSkippedMessage] = useState<string | null>(null);

  function addFiles(incoming: FileList | null) {
    if (!incoming) return;
    const all = Array.from(incoming);
    const accepted = all.filter(isAccepted);
    const skipped = all.length - accepted.length;

    if (accepted.length === 0) {
      if (skipped > 0) {
        setSkippedMessage(
          `${skipped} file${skipped === 1 ? "" : "s"} skipped — only PNG, JPG, or PDF are accepted.`
        );
      }
      return;
    }

    // De-dupe by name + size so the same file dropped twice isn't doubled.
    const existing = new Set(files.map((f) => `${f.name}:${f.size}`));
    const merged = [...files];
    for (const f of accepted) {
      const key = `${f.name}:${f.size}`;
      if (!existing.has(key)) {
        existing.add(key);
        merged.push(f);
      }
    }
    // A successful add clears any stale skip notice, then re-surfaces if this
    // batch also dropped some rejected files.
    setSkippedMessage(
      skipped > 0
        ? `${skipped} file${skipped === 1 ? "" : "s"} skipped — only PNG, JPG, or PDF are accepted.`
        : null
    );
    onChange(merged);
  }

  function removeAt(index: number) {
    onChange(files.filter((_, i) => i !== index));
  }

  return (
    <div className="flex flex-col gap-3">
      <div
        role="button"
        tabIndex={0}
        aria-label="Upload documents — click or drag and drop PNG, JPG, or PDF files"
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
          addFiles(e.dataTransfer.files);
        }}
        className={[
          "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-card border-2 border-dashed px-6 py-10 text-center transition-colors",
          dragging
            ? "border-coral bg-coral/5 dark:bg-creamtext/5"
            : "border-plum-800/20 dark:border-creamtext/20 bg-plum-800/[0.02] dark:bg-creamtext/5 hover:border-coral/60 hover:bg-coral/[0.03]",
        ].join(" ")}
      >
        <svg
          className={[
            "h-8 w-8 transition-colors",
            dragging ? "text-coral" : "text-plum-800/40 dark:text-creamtext/40",
          ].join(" ")}
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
        <p className="text-sm font-medium text-plum-800 dark:text-creamtext">
          <span className="text-coral">Click to upload</span> or drag &amp; drop
        </p>
        <p className="text-xs text-plum-800/50 dark:text-creamtext/50">PNG, JPG or PDF</p>
      </div>
      {/* The file input lives OUTSIDE the role=button dropzone (it is triggered via
          inputRef.click()), so the two interactive controls are never nested. */}
      <input
        id={id}
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPT}
        aria-label="Upload documents (PNG, JPG, or PDF)"
        className="hidden"
        onChange={(e) => {
          addFiles(e.target.files);
          // Reset so re-selecting the same file fires onChange again.
          e.target.value = "";
        }}
      />

      {/* Camera capture — on mobile this opens the rear camera; on desktop it
          falls back to the normal file picker. Captured photos flow through the
          exact same addFiles() path as drag/drop + browse. */}
      <div>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            cameraRef.current?.click();
          }}
          className="inline-flex items-center gap-2 rounded-full border border-plum-800/15 dark:border-creamtext/15 bg-white dark:bg-plum-800 px-4 py-2 text-sm font-medium text-plum-800 dark:text-creamtext transition-colors hover:border-coral/60 hover:text-coral"
        >
          <svg
            className="h-4 w-4"
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth={1.7}
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M6.827 6.175A2.31 2.31 0 0 1 5.186 7.23c-.38.054-.757.112-1.134.175C2.999 7.58 2.25 8.507 2.25 9.574V18a2.25 2.25 0 0 0 2.25 2.25h15A2.25 2.25 0 0 0 21.75 18V9.574c0-1.067-.75-1.994-1.802-2.169a47.865 47.865 0 0 0-1.134-.175 2.31 2.31 0 0 1-1.64-1.055l-.822-1.316a2.192 2.192 0 0 0-1.736-1.039 48.774 48.774 0 0 0-5.232 0 2.192 2.192 0 0 0-1.736 1.039l-.821 1.316Z"
            />
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M16.5 12.75a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0ZM18.75 10.5h.008v.008h-.008V10.5Z"
            />
          </svg>
          Take photo
        </button>
        <input
          ref={cameraRef}
          type="file"
          accept="image/*"
          capture="environment"
          aria-label="Take a photo of your document"
          title="Take a photo of your document"
          className="hidden"
          onChange={(e) => {
            addFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {skippedMessage && (
        <p
          role="alert"
          className="text-xs font-medium text-molten"
        >
          {skippedMessage}
        </p>
      )}

      {files.length > 0 && (
        <ul className="flex flex-col gap-2">
          {files.map((file, i) => (
            <li
              key={`${file.name}:${file.size}:${i}`}
              className="flex items-center gap-3 rounded-xl border border-plum-800/10 dark:border-creamtext/10 bg-white dark:bg-plum-800 px-3 py-2"
            >
              <Thumb file={file} />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-plum-800 dark:text-creamtext">
                  {file.name}
                </p>
                <p className="text-xs text-plum-800/50 dark:text-creamtext/50">
                  {formatSize(file.size)}
                </p>
              </div>
              <button
                type="button"
                onClick={() => removeAt(i)}
                aria-label={`Remove ${file.name}`}
                className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full text-plum-800/50 dark:text-creamtext/50 transition-colors hover:bg-crimson/10 hover:text-crimson"
              >
                <span aria-hidden className="text-lg leading-none">
                  &times;
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
