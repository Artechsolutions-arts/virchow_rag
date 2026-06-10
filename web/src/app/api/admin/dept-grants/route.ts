import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

// GET /api/admin/dept-grants  →  retrieval /admin/dept-grants
// POST /api/admin/dept-grants →  retrieval /admin/dept-grants

export async function GET(request: NextRequest) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) {
    return NextResponse.json({ detail: "Unauthorized" }, { status: 401 });
  }
  const res = await fetch(`${INTERNAL_URL}/admin/dept-grants`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const data = await res.json().catch(() => ({ detail: "Bad response" }));
  return NextResponse.json(data, { status: res.status });
}

export async function POST(request: NextRequest) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) {
    return NextResponse.json({ detail: "Unauthorized" }, { status: 401 });
  }
  const body = await request.json().catch(() => ({}));
  const res = await fetch(`${INTERNAL_URL}/admin/dept-grants`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({ detail: "Bad response" }));
  return NextResponse.json(data, { status: res.status });
}
