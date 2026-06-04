"use client";

import { useMemo, useState, useEffect } from "react";
import Dropzone from "react-dropzone";
import * as SettingsLayouts from "@/layouts/settings-layouts";
import { ADMIN_ROUTES } from "@/lib/admin-routes";
import { useUser } from "@/providers/UserProvider";
import DataTable from "@/refresh-components/table/DataTable";
import { createTableColumns } from "@/refresh-components/table/columns";
import Text from "@/refresh-components/texts/Text";
import { SvgUploadCloud, SvgFileText, SvgX } from "@opal/icons";
import Button from "@/refresh-components/buttons/Button";
import { formatBytes } from "@/lib/utils";
import { cn } from "@/lib/utils";

export type EmbeddingStatus = "IN PROGRESS" | "COMPLETED" | "FAILED TO UPLOAD";

export interface UploadedFileRecord {
  id: string;
  name: string;
  path: string;
  type: string;
  size: number;
  uploaded_by: string;
  uploaded_at: string;
  status: EmbeddingStatus;
  version: string;
  error_message?: string;
  current_stage?: string;
  ocr_current_page?: number;
  ocr_total_pages?: number;
  processing_started_at?: string;
  processing_finished_at?: string;
}

const STATUS_CONFIG: Record<EmbeddingStatus, { classes: string }> = {
  "IN PROGRESS": {
    classes: "text-status-info-05 bg-status-info-01 border-status-info-02",
  },
  "COMPLETED": {
    classes: "text-status-success-05 bg-status-success-01 border-status-success-02",
  },
  "FAILED TO UPLOAD": {
    classes: "text-status-error-05 bg-status-error-01 border-status-error-02 shadow-sm shadow-status-error-01",
  },
};

const STAGE_LABELS: Record<string, string> = {
  queued:       "QUEUED",
  preprocessing:"PREPROCESSING",
  ocr:          "OCR",
  assembling:   "ASSEMBLING",
  chunking:     "CHUNKING",
  embedding:    "EMBEDDING",
  storing:      "STORING",
  done:         "COMPLETED",
  skipped:      "COMPLETED",
  error:        "FAILED",
};

function formatElapsed(seconds: number): string {
  if (seconds < 0) return "";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m ${s}s`;
}

function getStageBadgeLabel(row: UploadedFileRecord): string {
  if (row.status === "COMPLETED") return "COMPLETED";
  if (row.status === "FAILED TO UPLOAD") return "FAILED TO UPLOAD";
  const stage = row.current_stage || "";
  if (stage === "ocr" && row.ocr_total_pages && row.ocr_total_pages > 0) {
    return `OCR — ${row.ocr_current_page ?? 0} / ${row.ocr_total_pages}`;
  }
  return STAGE_LABELS[stage] || "IN PROGRESS";
}

function getElapsedLabel(row: UploadedFileRecord): string {
  if (row.status === "COMPLETED" && row.processing_started_at && row.processing_finished_at) {
    const start = new Date(row.processing_started_at).getTime();
    const end   = new Date(row.processing_finished_at).getTime();
    if (!isNaN(start) && !isNaN(end) && end > start) {
      return formatElapsed((end - start) / 1000);
    }
  }
  if (row.status === "IN PROGRESS" && row.processing_started_at) {
    const start = new Date(row.processing_started_at).getTime();
    if (!isNaN(start)) {
      return formatElapsed((Date.now() - start) / 1000);
    }
  }
  return "";
}

const PAGE_SIZE = 50;

export function FileUploadContent() {
  const { user } = useUser();
  const [files, setFiles] = useState<UploadedFileRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);

  const fetchFiles = async () => {
    try {
      const response = await fetch("/api/admin/rag/list");
      if (response.ok) {
        const data = await response.json();
        setFiles((prev) => {
          const next = data.uploads || [];
          if (next.length !== prev.length) setPage(0);
          return next;
        });
      }
    } catch (error) {
      console.error("Failed to fetch uploads:", error);
      setFiles([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchFiles();
    const interval = setInterval(fetchFiles, 5000);
    return () => clearInterval(interval);
  }, []);

  const updateStatus = (id: string, newStatus: EmbeddingStatus) => {
    setFiles((prev) =>
      prev.map((f) => (f.id === id ? { ...f, status: newStatus } : f))
    );
  };

  const handleRestart = async (id: string) => {
    try {
      updateStatus(id, "IN PROGRESS");
      const formData = new FormData();
      formData.append("upload_ids", id);
      
      const response = await fetch("/api/admin/rag/restart", {
        method: "POST",
        body: formData,
      });
      
      if (response.ok) {
        fetchFiles();
      }
    } catch (error) {
      console.error("Restart error:", error);
    }
  };

  const handleUpload = async (acceptedFiles: File[]) => {
    const BATCH_SIZE = 50;
    const MAX_CONCURRENT = 4;

    const sendBatch = async (files: File[]) => {
      const formData = new FormData();
      files.forEach((f) => formData.append("files", f));
      try {
        await fetch("/api/admin/rag/upload", { method: "POST", body: formData });
      } catch (error) {
        console.error("Upload batch error:", error);
      }
    };

    // Split into fixed-size batches
    const batches: File[][] = [];
    for (let i = 0; i < acceptedFiles.length; i += BATCH_SIZE) {
      batches.push(acceptedFiles.slice(i, i + BATCH_SIZE));
    }

    // Process waves of MAX_CONCURRENT batches at a time
    for (let i = 0; i < batches.length; i += MAX_CONCURRENT) {
      await Promise.all(batches.slice(i, i + MAX_CONCURRENT).map(sendBatch));
    }

    fetchFiles();
  };

  const onDrop = (acceptedFiles: File[]) => {
    handleUpload(acceptedFiles);
  };

  const removeFile = (id: string) => {
    setFiles((prev) => prev.filter((f) => f.id !== id));
  };

  const columns = useMemo(() => {
    const tc = createTableColumns<UploadedFileRecord>();
    return [
      tc.column("name", {
        header: "FILE NAME",
        weight: 20,
        minWidth: 200,
        cell: (value) => (
          <div className="flex items-center gap-2">
            <SvgFileText size={16} className="text-text-03" />
            <Text mainUiBody text01 className="font-bold">
              {value}
            </Text>
          </div>
        ),
      }),
      tc.column("path", {
        header: "FILE PATH",
        weight: 15,
        minWidth: 150,
        cell: (value) => <Text secondaryBody text03 className="truncate max-w-[150px]">{value}</Text>,
      }),
      tc.column("type", {
        header: "FILE TYPE",
        weight: 10,
        minWidth: 100,
        cell: (value) => <Text secondaryBody text02>{value}</Text>,
      }),
      tc.column("size", {
        header: "FILE SIZE",
        weight: 10,
        minWidth: 100,
        cell: (value) => <Text secondaryBody text02>{formatBytes(value)}</Text>,
      }),
      tc.column("uploaded_by", {
        header: "UPLOADED BY",
        weight: 15,
        minWidth: 150,
        cell: (value) => <Text secondaryBody text03 className="truncate max-w-[120px]">{value}</Text>,
      }),
      tc.column("uploaded_at", {
        header: "UPLOADED AT",
        weight: 15,
        minWidth: 150,
        cell: (value) => (
          <Text secondaryBody text03>
            {value && value !== "0" ? new Date(value).toLocaleString() : "Never"}
          </Text>
        ),
      }),
      tc.column("status", {
        header: "STATUS",
        weight: 18,
        minWidth: 190,
        cell: (value, row) => {
          const config = STATUS_CONFIG[value as EmbeddingStatus] || STATUS_CONFIG["IN PROGRESS"];
          const label   = getStageBadgeLabel(row);
          return (
            <div className="flex flex-col gap-1 group relative">
              <div
                className={cn(
                  "px-3 py-1 rounded-full border text-[10px] uppercase font-bold w-fit transition-colors flex items-center gap-1.5",
                  config.classes
                )}
                title={row.error_message}
              >
                {value === "IN PROGRESS" && (
                  <span className="inline-block w-1.5 h-1.5 rounded-full bg-current animate-pulse" />
                )}
                {label}
              </div>
              {row.error_message && (
                <div className="hidden group-hover:block absolute top-full left-0 z-[100] mt-2 p-3 bg-background-neutral-01 border border-status-error-02 rounded shadow-2xl max-w-[400px] max-h-[200px] overflow-auto text-[10px] whitespace-pre-wrap font-mono text-status-error-05">
                  <Text text01 className="font-bold mb-1">Reason for failure:</Text>
                  {row.error_message}
                </div>
              )}
            </div>
          );
        },
      }),
      tc.column("processing_started_at", {
        header: "TIME TAKEN",
        weight: 10,
        minWidth: 100,
        cell: (_value, row) => {
          const elapsed = getElapsedLabel(row);
          return elapsed
            ? <Text secondaryBody text02>{elapsed}</Text>
            : <Text secondaryBody text03>—</Text>;
        },
      }),
      tc.column("version", {
        header: "VERSION",
        weight: 8,
        minWidth: 80,
        cell: (value) => <Text secondaryBody text03>{value}</Text>,
      }),
      tc.actions({
        cell: (row) => (
          <div className="flex justify-end pr-2 gap-2">
            {row.status === "FAILED TO UPLOAD" && (
              <Button
                size="md"
                action
                secondary
                className="text-[10px] h-7 px-2 border-border-03 hover:bg-background-neutral-02"
                onClick={() => handleRestart(row.id)}
              >
                RETRY
              </Button>
            )}
            <Button
              danger
              tertiary
              size="md"
              leftIcon={SvgX}
              onClick={() => removeFile(row.id)}
            >
              {" "}
            </Button>
          </div>
        ),
      }),
    ];
  }, [files]);

  return (
    <Dropzone onDrop={onDrop} noClick noKeyboard>
      {({ getRootProps, getInputProps, isDragActive, open }) => (
        <div 
          {...getRootProps()} 
          className={cn(
            "relative flex-1 flex flex-col min-h-[600px] h-full overflow-hidden p-6 gap-6",
          )}
        >
          <input {...getInputProps()} />
          
          <div 
            className={cn(
              "border-2 border-dashed border-border-02 rounded-lg p-10 transition-all flex flex-col items-center justify-center gap-4 mb-6 cursor-pointer hover:border-theme-primary-03 hover:bg-background-neutral-02/50",
              isDragActive && "border-theme-primary-05 bg-theme-primary-01/10"
            )}
            onClick={open}
          >
            <div className="p-4 rounded-full bg-background-neutral-02">
              <SvgUploadCloud size={40} className="text-theme-primary-05" />
            </div>
            <div className="text-center">
              <Text mainUiAction text01 as="p">
                Click or drag and drop files here to upload
              </Text>
              <Text secondaryBody text03 as="p">
                High speed embedding for all document types
              </Text>
            </div>
          </div>

          <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
            <div className="flex items-center justify-between mb-4">
              <Text headingH3 text01>Uploaded Documents</Text>
              {files.length > 0 && (
                <Text secondaryBody text03>
                  {`${files.length} items`}
                </Text>
              )}
            </div>
            {files && files.length > 0 ? (
              <>
                <div className="flex-1 overflow-auto border border-border-02 rounded-lg bg-background-neutral-01">
                  <DataTable
                    columns={columns}
                    data={files.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)}
                    getRowId={(row) => row.id}
                  />
                </div>
                {files.length > PAGE_SIZE && (
                  <div className="flex items-center justify-between mt-3 px-1">
                    <Text secondaryBody text03>
                      Showing {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, files.length)} of {files.length}
                    </Text>
                    <div className="flex gap-2">
                      <button
                        disabled={page === 0}
                        onClick={() => setPage((p) => p - 1)}
                        className="px-3 py-1 text-[11px] font-medium rounded border border-border-02 bg-background-neutral-01 text-text-02 disabled:opacity-30 hover:bg-background-neutral-02 transition-colors"
                      >
                        ← Prev
                      </button>
                      <button
                        disabled={(page + 1) * PAGE_SIZE >= files.length}
                        onClick={() => setPage((p) => p + 1)}
                        className="px-3 py-1 text-[11px] font-medium rounded border border-border-02 bg-background-neutral-01 text-text-02 disabled:opacity-30 hover:bg-background-neutral-02 transition-colors"
                      >
                        Next →
                      </button>
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div className="flex-1 flex flex-col items-center justify-center p-20 border border-border-02 border-dashed rounded-lg opacity-40">
                <Text secondaryBody text03>No documents in queue.</Text>
              </div>
            )}
          </div>

          <div 
            className={cn(
              "absolute inset-0 z-50 flex items-center justify-center backdrop-blur-md bg-theme-primary-01/20 border-4 border-dashed border-theme-primary-05 rounded-lg pointer-events-none transition-all duration-300 opacity-0",
              isDragActive && "opacity-100"
            )}
          >
            <div className="bg-background-neutral-01 p-10 rounded-2xl shadow-[0_0_50px_rgba(0,0,0,0.1)] border border-border-01 flex flex-col items-center gap-4 transform scale-110">
              <div className="bg-theme-primary-05/10 p-6 rounded-full animate-bounce">
                <SvgUploadCloud size={64} className="text-theme-primary-05" />
              </div>
              <Text headingH2 text01>Release to start indexing</Text>
              <Text mainUiBody text03>Your files will be processed immediately</Text>
            </div>
          </div>
        </div>
      )}
    </Dropzone>
  );
}

export function FileUploadPage() {
  return (
    <SettingsLayouts.Root width="full">
      <SettingsLayouts.Header
        icon={ADMIN_ROUTES.UPLOAD_FILES.icon}
        title={ADMIN_ROUTES.UPLOAD_FILES.title}
        separator
      />
      <SettingsLayouts.Body>
        <FileUploadContent />
      </SettingsLayouts.Body>
    </SettingsLayouts.Root>
  );
}
