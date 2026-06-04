import { NextRequest, NextResponse } from "next/server";

const SEAWEEDFS_URL =
  process.env.SEAWEEDFS_FILER_URL || "http://192.168.10.10:8889";
const SEAWEEDFS_BUCKET = process.env.SEAWEEDFS_BUCKET || "rag-docs";

// GET /api/chat/file/download?id={uuid}/{filename}
// Proxies file download from SeaweedFS so attached chat files can be viewed.
export async function GET(request: NextRequest) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) {
    return NextResponse.json({ detail: "Unauthorized" }, { status: 401 });
  }

  const filePath = request.nextUrl.searchParams.get("id");
  if (!filePath) {
    return NextResponse.json({ detail: "Missing id parameter" }, { status: 400 });
  }

  const seaweedUrl = `${SEAWEEDFS_URL}/buckets/${SEAWEEDFS_BUCKET}/raw/${filePath}`;

  try {
    const res = await fetch(seaweedUrl);
    if (!res.ok) {
      return NextResponse.json(
        { detail: `File not found: ${filePath}` },
        { status: 404 }
      );
    }

    const contentType =
      res.headers.get("content-type") || "application/octet-stream";
    const buffer = await res.arrayBuffer();
    const fileName = filePath.split("/").at(-1) ?? "file";

    return new NextResponse(buffer, {
      status: 200,
      headers: {
        "Content-Type": contentType,
        "Content-Disposition": `inline; filename="${fileName}"`,
      },
    });
  } catch (e) {
    const message = e instanceof Error ? e.message : "Unknown error";
    return NextResponse.json({ detail: message }, { status: 502 });
  }
}
