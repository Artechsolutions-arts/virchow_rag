import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

export async function POST(request: NextRequest) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  try {
    const res = await fetch(`${INTERNAL_URL}/chats/create`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) throw new Error("Backend error");
    return NextResponse.json(await res.json());
  } catch {
    return NextResponse.json({ error: "Backend error" }, { status: 500 });
  }
}
