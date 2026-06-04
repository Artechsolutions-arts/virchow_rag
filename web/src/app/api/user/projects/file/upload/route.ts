import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

// Shape the frontend expects for each uploaded file
interface ProjectFileResponse {
  id: string;
  file_id: string;
  name: string;
  project_id: number | null;
  user_id: string | null;
  created_at: string;
  status: string;
  file_type: string;
  last_accessed_at: string;
  chat_file_type: string;
  token_count: number | null;
  chunk_count: number | null;
  temp_id: string | null;
}

export async function POST(request: NextRequest) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) {
    return NextResponse.json({ user_files: [], rejected_files: [] }, { status: 401 });
  }

  try {
    const incomingForm = await request.formData();
    const allFiles = incomingForm.getAll("files");

    if (allFiles.length === 0) {
      return NextResponse.json({ user_files: [], rejected_files: [] });
    }

    // temp_id_map: { "size|namePrefix": "temp-uuid" }
    const tempIdMapRaw = incomingForm.get("temp_id_map");
    const tempIdMap: Record<string, string> = tempIdMapRaw
      ? JSON.parse(tempIdMapRaw as string)
      : {};

    const user_files: ProjectFileResponse[] = [];
    const rejected_files: { file_name: string; reason: string }[] = [];
    const now = new Date().toISOString();

    const CONCURRENCY = 5;

    const uploadOne = async (entry: FormDataEntryValue) => {
      const file = entry as File;
      const fileKey = `${file.size}|${file.name.slice(0, 50)}`;
      const tempId = tempIdMap[fileKey] ?? null;

      try {
        const proxyForm = new FormData();
        proxyForm.append("file", file);
        const res = await fetch(`${INTERNAL_URL}/documents/upload`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
          body: proxyForm,
        });

        const data = await res.json().catch(() => ({}));

        if (!res.ok) {
          rejected_files.push({
            file_name: file.name,
            reason: data.detail || `Upload failed (${res.status})`,
          });
          return;
        }

        // Ingest API returns { files: [{ file_id, filename }] }
        // Retrieval local fallback returns { ok, file_name, file_path }
        const fileId: string =
          data.files?.[0]?.file_id ??
          data.file_id ??
          `${Date.now()}-${Math.random().toString(36).slice(2)}`;

        user_files.push({
          id: fileId,
          file_id: fileId,
          name: file.name,
          project_id: null,
          user_id: null,
          created_at: now,
          status: "PROCESSING",
          file_type: file.type || "application/pdf",
          last_accessed_at: now,
          chat_file_type: "document",
          token_count: null,
          chunk_count: null,
          temp_id: tempId,
        });
      } catch {
        rejected_files.push({
          file_name: file.name,
          reason: "Upload failed. Please retry.",
        });
      }
    };

    for (let i = 0; i < allFiles.length; i += CONCURRENCY) {
      await Promise.all(allFiles.slice(i, i + CONCURRENCY).map(uploadOne));
    }

    return NextResponse.json({ user_files, rejected_files });
  } catch (e) {
    const message = e instanceof Error ? e.message : "Unknown error";
    console.error("User file upload error:", message);
    return NextResponse.json({ user_files: [], rejected_files: [] }, { status: 502 });
  }
}
