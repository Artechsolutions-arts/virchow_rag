import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

export async function POST(request: NextRequest) {
  const body = await request.text();
  const params = new URLSearchParams(body);
  const email = params.get("username") || "";
  const password = params.get("password") || "";

  try {
    const res = await fetch(`${INTERNAL_URL}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      return NextResponse.json(
        { detail: err.detail || "LOGIN_BAD_CREDENTIALS" },
        { status: res.status }
      );
    }

    const data = await res.json();
    const response = NextResponse.json({ message: "Logged in" }, { status: 200 });
    response.cookies.set("fastapiusersauth", data.token, {
      httpOnly: true,
      sameSite: "lax",
      path: "/",
      maxAge: 60 * 60 * 24,
    });
    return response;
  } catch (e) {
    return NextResponse.json({ detail: "Backend unavailable" }, { status: 503 });
  }
}
