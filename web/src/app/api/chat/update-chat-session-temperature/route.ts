import { NextRequest, NextResponse } from "next/server";

// Temperature is fixed at 0.0 in this deployment — acknowledge silently.
export async function PUT(request: NextRequest) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  return NextResponse.json({ ok: true });
}
