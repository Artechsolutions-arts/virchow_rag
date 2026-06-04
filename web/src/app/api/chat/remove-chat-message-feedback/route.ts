import { NextRequest, NextResponse } from "next/server";

// Feedback is not yet implemented in the backend — acknowledge silently.
export async function DELETE(request: NextRequest) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  return NextResponse.json({ ok: true });
}
