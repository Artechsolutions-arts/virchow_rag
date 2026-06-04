import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

export async function GET(request: NextRequest) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) return NextResponse.json({ detail: "Unauthorized" }, { status: 401 });

  const res = await fetch(`${INTERNAL_URL}/admin/users-list`, {
    headers: { Authorization: `Bearer ${token}` },
  }).catch(() => null);

  if (!res) return NextResponse.json({ detail: "Backend unavailable" }, { status: 503 });

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
