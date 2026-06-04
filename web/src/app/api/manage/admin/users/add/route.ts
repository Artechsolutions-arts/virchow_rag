import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

export async function POST(request: NextRequest) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) return NextResponse.json({ detail: "Unauthorized" }, { status: 401 });

  const body = await request.json().catch(() => null);
  if (!body) return NextResponse.json({ detail: "Invalid request body" }, { status: 400 });

  const res = await fetch(`${INTERNAL_URL}/admin/users`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  }).catch(() => null);

  if (!res) return NextResponse.json({ detail: "Backend unavailable" }, { status: 503 });

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
