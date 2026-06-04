import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

export async function POST(request: NextRequest) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) {
    return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
  }

  const body = await request.json().catch(() => ({}));
  const { question, chat_id } = body;

  try {
    const fd = new FormData();
    fd.append("question", question || "");
    if (chat_id) fd.append("chat_id", chat_id);

    const res = await fetch(`${INTERNAL_URL}/query`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: fd,
    });

    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (e) {
    return NextResponse.json({ detail: "Backend unavailable" }, { status: 503 });
  }
}
