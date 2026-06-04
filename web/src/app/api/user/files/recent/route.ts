import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

export async function GET(request: NextRequest) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) {
    return NextResponse.json([]);
  }

  try {
    const res = await fetch(`${INTERNAL_URL}/documents?limit=50`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return NextResponse.json([]);

    const data = await res.json().catch(() => ({ uploads: [] }));
    const uploads = data.uploads || [];

    // Map to ProjectFile shape so the "Recent Files" popover shows uploads
    const files = uploads
      .filter((u: any) => u.status === "COMPLETED")
      .map((u: any) => ({
        id: u.id,
        file_id: u.id,
        name: u.name,
        project_id: null,
        user_id: null,
        created_at: u.uploaded_at || new Date().toISOString(),
        status: "COMPLETED",
        file_type: u.type || "application/pdf",
        last_accessed_at: u.uploaded_at || new Date().toISOString(),
        chat_file_type: "document",
        token_count: null,
        chunk_count: null,
        temp_id: null,
      }));

    return NextResponse.json(files);
  } catch {
    return NextResponse.json([]);
  }
}
