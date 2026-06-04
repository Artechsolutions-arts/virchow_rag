import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

export async function POST(request: NextRequest) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) return NextResponse.json({ detail: "Unauthorized" }, { status: 401 });

  try {
    const res = await fetch(`${INTERNAL_URL}/auth/refresh`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return NextResponse.json({ detail: "Refresh failed" }, { status: res.status });

    const data = await res.json();
    const response = NextResponse.json({ message: "Token refreshed" }, { status: 200 });
    response.cookies.set("fastapiusersauth", data.token, {
      httpOnly: true,
      sameSite: "lax",
      path: "/",
      maxAge: 60 * 60 * 24,
    });
    return response;
  } catch {
    return NextResponse.json({ detail: "Backend unavailable" }, { status: 503 });
  }
}
