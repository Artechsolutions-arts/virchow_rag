import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

// POST /api/chat/file
// Chatbar file attachment upload. Forwards each file to the RAG backend
// document ingestion endpoint and returns FileDescriptor objects so the
// frontend can show the file as an attachment badge in the chat message.
export async function POST(request: NextRequest) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) {
    return NextResponse.json({ detail: "Unauthorized" }, { status: 401 });
  }

  try {
    const incomingForm = await request.formData();
    const fileEntries = incomingForm.getAll("files");

    if (!fileEntries.length) {
      return NextResponse.json(
        { detail: "No files provided" },
        { status: 422 }
      );
    }

    const descriptors = [];

    for (const fileEntry of fileEntries) {
      const proxyForm = new FormData();
      proxyForm.append("file", fileEntry);

      const res = await fetch(`${INTERNAL_URL}/documents/upload`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: proxyForm,
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        return NextResponse.json(
          { detail: err.detail || "Upload failed" },
          { status: res.status }
        );
      }

      const data = await res.json();
      const fileName =
        fileEntry instanceof File ? fileEntry.name : (data.file_name ?? "file");

      descriptors.push({
        id: data.file_path ?? data.file_name ?? fileName,
        type: "document",
        name: fileName,
      });
    }

    return NextResponse.json({ files: descriptors });
  } catch (e) {
    const message = e instanceof Error ? e.message : "Unknown error";
    return NextResponse.json({ detail: message }, { status: 502 });
  }
}
