import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

export async function PUT(request: NextRequest) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  // F-C3: return 401 instead of silently succeeding when unauthenticated
  if (!token) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  try {
    const body = await request.json();
    const chatId = body.chat_session_id;
    const name = (body.name ?? "").trim();

    if (!chatId || !name) {
      return NextResponse.json(
        { error: "chat_session_id and name are required" },
        { status: 422 }
      );
    }

    const res = await fetch(`${INTERNAL_URL}/chats/${chatId}/rename`, {
      method: "PUT",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      return NextResponse.json(
        { error: err.detail ?? "Rename failed" },
        { status: res.status }
      );
    }

    return NextResponse.json({ ok: true });
  } catch {
    return NextResponse.json({ error: "Internal error" }, { status: 500 });
  }
}
