import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

// DELETE /api/admin/dept-grants/<id> → retrieval /admin/dept-grants/<id>

export async function DELETE(
  request: NextRequest,
  ctx: { params: Promise<{ id: string }> }
) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) {
    return NextResponse.json({ detail: "Unauthorized" }, { status: 401 });
  }
  const { id } = await ctx.params;
  const res = await fetch(`${INTERNAL_URL}/admin/dept-grants/${id}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  const data = await res.json().catch(() => ({ detail: "Bad response" }));
  return NextResponse.json(data, { status: res.status });
}
