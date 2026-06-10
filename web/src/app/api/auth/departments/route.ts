import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

// GET /api/auth/departments → retrieval /auth/departments
// Lists every active department. Requires a valid auth cookie; admin-only
// callers (like /admin/permissions) rely on this for the picker.

export async function GET(request: NextRequest) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) {
    return NextResponse.json({ detail: "Unauthorized" }, { status: 401 });
  }
  const res = await fetch(`${INTERNAL_URL}/auth/departments`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const data = await res.json().catch(() => ({ detail: "Bad response" }));
  return NextResponse.json(data, { status: res.status });
}
