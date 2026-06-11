import { NextRequest, NextResponse } from "next/server";

const SEAWEEDFS_URL =
  process.env.SEAWEEDFS_FILER_URL || "http://192.168.10.10:8889";
const SEAWEEDFS_BUCKET = process.env.SEAWEEDFS_BUCKET || "rag-docs";
const INTERNAL_URL =
  process.env.INTERNAL_URL || "http://retrieval:8080";

// GET /api/chat/file/download?id={filename}
// Serves a document file. Tries SeaweedFS first; falls back to the retrieval
// service which can serve from local disk for files not yet in SeaweedFS.
export async function GET(request: NextRequest) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) {
    return NextResponse.json({ detail: "Unauthorized" }, { status: 401 });
  }

  const filePath = request.nextUrl.searchParams.get("id");
  if (!filePath) {
    return NextResponse.json({ detail: "Missing id parameter" }, { status: 400 });
  }

  const fileName = filePath.split("/").at(-1) ?? "file";

  // 1. Try SeaweedFS directly (fast path)
  const seaweedUrl = `${SEAWEEDFS_URL}/buckets/${SEAWEEDFS_BUCKET}/raw/${encodeURIComponent(fileName)}`;
  try {
    const res = await fetch(seaweedUrl);
    if (res.ok) {
      const contentType =
        res.headers.get("content-type") || "application/octet-stream";
      const buffer = await res.arrayBuffer();
      return new NextResponse(buffer, {
        status: 200,
        headers: {
          "Content-Type": contentType,
          "Content-Disposition": `inline; filename="${fileName}"`,
        },
      });
    }
  } catch {
    // SeaweedFS unreachable — fall through to retrieval fallback
  }

  // 2. Fall back to retrieval service (serves from local disk + backfills SeaweedFS)
  try {
    const fallbackUrl = `${INTERNAL_URL}/documents/serve/${encodeURIComponent(fileName)}`;
    const fallbackRes = await fetch(fallbackUrl, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (fallbackRes.ok) {
      const contentType =
        fallbackRes.headers.get("content-type") || "application/octet-stream";
      const buffer = await fallbackRes.arrayBuffer();
      return new NextResponse(buffer, {
        status: 200,
        headers: {
          "Content-Type": contentType,
          "Content-Disposition": `inline; filename="${fileName}"`,
        },
      });
    }
  } catch {
    // retrieval also unreachable
  }

  return NextResponse.json(
    { detail: `File not found: ${filePath}` },
    { status: 404 }
  );
}
