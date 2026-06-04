import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

export async function GET(request: NextRequest) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  // F-C3: return 401 instead of empty sessions when unauthenticated
  if (!token) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  try {
    const res = await fetch(`${INTERNAL_URL}/chats`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return NextResponse.json({ sessions: [], has_more: false });

    const chats: any[] = await res.json();
    const sessions = chats.map((c) => ({
      id: c.id,
      name: c.title || "New Chat",
      persona_id: 0,
      time_created: c.created_at,
      time_updated: c.updated_at ?? c.created_at,
      shared_status: "private",
      project_id: null,
      current_alternate_model: "",
      current_temperature_override: null,
    }));
    return NextResponse.json({ sessions, has_more: false });
  } catch {
    return NextResponse.json({ sessions: [], has_more: false });
  }
}
