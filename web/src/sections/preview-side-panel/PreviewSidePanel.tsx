"use client";

import {
  useState,
  useEffect,
  useCallback,
  useMemo,
} from "react";
import mime from "mime";
import { MinimalVirchowDocument } from "@/lib/search/interfaces";
import SimpleLoader from "@/refresh-components/loaders/SimpleLoader";
import {
  getCodeLanguage,
  getDataLanguage,
  getLanguageByMime,
} from "@/lib/languages";
import { fetchChatFile } from "@/lib/chat/svc";
import { PreviewContext } from "@/sections/modals/PreviewModal/interfaces";
import { resolveVariant } from "@/sections/modals/PreviewModal/variants";
import { cn } from "@/lib/utils";

/**
 * A right-anchored side panel for previewing source documents, modeled on
 * Claude Code's artifacts pane. Reuses PreviewModal's variant + fetch logic
 * but renders as a fixed-width slide-in pane that lives alongside the chat
 * instead of overlaying it.
 *
 * The chat layout shrinks to make room for the panel via the parent's grid
 * (see AppPage.tsx). When ``presentingDocument`` is null the panel renders
 * collapsed (zero width) so the transition animates cleanly.
 */
interface PreviewSidePanelProps {
  presentingDocument: MinimalVirchowDocument | null;
  onClose: () => void;
  /** Panel width when open. Defaults to 45vw, max 720px. */
  widthClass?: string;
}

export default function PreviewSidePanel({
  presentingDocument,
  onClose,
  widthClass = "w-[45vw] max-w-[720px] min-w-[400px]",
}: PreviewSidePanelProps) {
  const [fileContent, setFileContent] = useState("");
  const [fileUrl, setFileUrl] = useState("");
  const [fileName, setFileName] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mimeType, setMimeType] = useState("application/octet-stream");
  const [zoom, setZoom] = useState(100);

  const isOpen = presentingDocument !== null;

  const semanticIdentifier = presentingDocument?.semantic_identifier ?? "";
  const variant = useMemo(
    () => resolveVariant(semanticIdentifier, mimeType),
    [semanticIdentifier, mimeType]
  );

  const language = useMemo(
    () =>
      getCodeLanguage(semanticIdentifier || "") ||
      getLanguageByMime(mimeType) ||
      getDataLanguage(semanticIdentifier || "") ||
      "plaintext",
    [mimeType, semanticIdentifier]
  );

  const lineCount = useMemo(
    () => (fileContent ? fileContent.split("\n").length : 0),
    [fileContent]
  );

  const fileSize = useMemo(() => {
    if (!fileContent) return "";
    const bytes = new TextEncoder().encode(fileContent).length;
    if (bytes < 1024) return `${bytes} B`;
    const kb = bytes / 1024;
    if (kb < 1024) return `${kb.toFixed(2)} KB`;
    const mb = kb / 1024;
    return `${mb.toFixed(2)} MB`;
  }, [fileContent]);

  const fetchFile = useCallback(async () => {
    if (!presentingDocument) return;
    setIsLoading(true);
    setLoadError(null);
    setFileContent("");
    const fileIdLocal =
      presentingDocument.document_id.split("__")[1] ||
      presentingDocument.document_id;

    try {
      const response = await fetchChatFile(fileIdLocal);
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      setFileUrl((prev) => {
        if (prev) window.URL.revokeObjectURL(prev);
        return url;
      });

      const originalFileName =
        presentingDocument.semantic_identifier || "document";
      setFileName(originalFileName);

      const rawContentType =
        response.headers.get("Content-Type") || "application/octet-stream";
      const resolvedMime =
        rawContentType === "application/octet-stream"
          ? mime.getType(originalFileName) ?? rawContentType
          : rawContentType;
      setMimeType(resolvedMime);

      const resolved = resolveVariant(
        presentingDocument.semantic_identifier,
        resolvedMime
      );
      if (resolved.needsTextContent) {
        setFileContent(await blob.text());
      }
    } catch {
      setLoadError("Failed to load document.");
    } finally {
      setIsLoading(false);
    }
  }, [presentingDocument]);

  useEffect(() => {
    if (presentingDocument) {
      fetchFile();
    }
  }, [presentingDocument, fetchFile]);

  useEffect(() => {
    return () => {
      if (fileUrl) window.URL.revokeObjectURL(fileUrl);
    };
  }, [fileUrl]);

  // Esc to close
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isOpen, onClose]);

  const handleZoomIn = useCallback(
    () => setZoom((p) => Math.min(p + 25, 200)),
    []
  );
  const handleZoomOut = useCallback(
    () => setZoom((p) => Math.max(p - 25, 25)),
    []
  );

  const ctx: PreviewContext = useMemo(
    () => ({
      fileContent,
      fileUrl,
      fileName,
      language,
      lineCount,
      fileSize,
      zoom,
      onZoomIn: handleZoomIn,
      onZoomOut: handleZoomOut,
    }),
    [
      fileContent,
      fileUrl,
      fileName,
      language,
      lineCount,
      fileSize,
      zoom,
      handleZoomIn,
      handleZoomOut,
    ]
  );

  if (!isOpen) return null;

  return (
    <aside
      data-preview-side-panel="true"
      className={cn(
        "h-full flex flex-col flex-shrink-0",
        "bg-background-tint-01 border-l border-border-200",
        "shadow-xl",
        widthClass
      )}
      aria-label="Source document preview"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border-200 bg-background-tint-00">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-text-900 truncate">
            {fileName || "Document"}
          </div>
          {variant.headerDescription(ctx) && (
            <div className="text-xs text-text-400 truncate">
              {variant.headerDescription(ctx)}
            </div>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          className="ml-3 p-1.5 rounded-md hover:bg-background-tint-02 text-text-500"
          aria-label="Close preview"
          title="Close (Esc)"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path
              d="M4 4l8 8M12 4l-8 8"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
            />
          </svg>
        </button>
      </div>

      {/* Body */}
      <div className="flex flex-col flex-1 min-h-0 overflow-hidden w-full bg-background-tint-01 relative">
        {isLoading ? (
          <div className="flex-1 flex items-center justify-center">
            <SimpleLoader className="h-8 w-8" />
          </div>
        ) : loadError ? (
          <div className="p-4 text-sm text-text-500">{loadError}</div>
        ) : (
          variant.renderContent(ctx)
        )}

        {/* Footer slot */}
        {!isLoading && !loadError && (
          <div
            className="absolute bottom-0 left-0 right-0 flex items-center justify-between p-3 pointer-events-none"
            style={{
              background:
                "linear-gradient(to top, var(--background-tint-01) 50%, transparent)",
            }}
          >
            <div className="pointer-events-auto">
              {variant.renderFooterLeft(ctx)}
            </div>
            <div className="pointer-events-auto rounded-12 bg-background-tint-00 p-1 shadow-lg">
              {variant.renderFooterRight(ctx)}
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}
