import { NextRequest, NextResponse } from "next/server";

const SEAWEEDFS_URL =
  process.env.SEAWEEDFS_FILER_URL || "http://192.168.10.10:8889";
const SEAWEEDFS_BUCKET = process.env.SEAWEEDFS_BUCKET || "rag-docs";

// GET /api/chat/file/{uuid}/{filename}
// Proxies file download from SeaweedFS so attached chat files can be viewed.
// The path segments are joined to reconstruct the SeaweedFS file_path.
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) {
    return NextResponse.json({ detail: "Unauthorized" }, { status: 401 });
  }

  const { path } = await params;
  // Each segment is already decoded by Next.js router; re-encode for the URL
  const seaweedUrl = `${SEAWEEDFS_URL}/buckets/${SEAWEEDFS_BUCKET}/raw/${path.map(encodeURIComponent).join("/")}`;

  try {
    const res = await fetch(seaweedUrl);
    if (!res.ok) {
      return NextResponse.json(
        { detail: `File not found: ${path.join("/")}` },
        { status: 404 }
      );
    }

    const contentType =
      res.headers.get("content-type") || "application/octet-stream";
    const buffer = await res.arrayBuffer();

    return new NextResponse(buffer, {
      status: 200,
      headers: {
        "Content-Type": contentType,
        "Content-Disposition": `inline; filename="${path.at(-1) ?? "file"}"`,
      },
    });
  } catch (e) {
    const message = e instanceof Error ? e.message : "Unknown error";
    return NextResponse.json({ detail: message }, { status: 502 });
  }
}
