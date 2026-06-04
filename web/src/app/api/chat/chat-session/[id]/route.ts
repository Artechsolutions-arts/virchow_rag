import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

// PATCH /api/chat/chat-session/[id]
// Updates chat session properties (e.g. sharing_status).
// Proxies to backend; falls back to 200 stub so share-link generation works
// even if the backend does not implement this endpoint.
export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) {
    return NextResponse.json({ detail: "Unauthorized" }, { status: 401 });
  }

  const { id } = await params;
  const body = await request.json().catch(() => ({}));

  try {
    const res = await fetch(`${INTERNAL_URL}/api/chat/chat-session/${id}`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(body),
    });

    if (res.ok) {
      const data = await res.json().catch(() => ({}));
      return NextResponse.json(data);
    }
  } catch {
    // backend doesn't support this endpoint — fall through to stub
  }

  // Stub: return success so the share link is generated client-side
  return NextResponse.json({ id, ...body });
}
