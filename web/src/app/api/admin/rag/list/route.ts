import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

export async function GET(request: NextRequest) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) {
    return NextResponse.json({ uploads: [] }, { status: 401 });
  }

  try {
    const res = await fetch(`${INTERNAL_URL}/documents?limit=10000`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const data = await res.json().catch(() => ({ uploads: [] }));
    return NextResponse.json(data, { status: res.status });
  } catch (e) {
    return NextResponse.json({ uploads: [] }, { status: 502 });
  }
}
