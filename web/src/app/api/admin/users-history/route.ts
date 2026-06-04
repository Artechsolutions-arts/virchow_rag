import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

export async function GET(request: NextRequest) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) return NextResponse.json({ detail: "Unauthorized" }, { status: 401 });

  const res = await fetch(`${INTERNAL_URL}/api/admin/users-history`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const data = await res.json().catch(() => []);
  return NextResponse.json(data, { status: res.status });
}
