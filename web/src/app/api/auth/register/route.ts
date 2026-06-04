import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

export async function POST(request: NextRequest) {
  const body = await request.json().catch(() => ({}));
  const email = body.email || "";
  const password = body.password || "";
  const name = body.name || email.split("@")[0];

  try {
    const res = await fetch(`${INTERNAL_URL}/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, name }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      if (res.status === 409) {
        return NextResponse.json(
          { detail: "REGISTER_USER_ALREADY_EXISTS" },
          { status: 400 }
        );
      }
      return NextResponse.json(
        { detail: err.detail || "Registration failed" },
        { status: res.status }
      );
    }

    const data = await res.json();
    const response = NextResponse.json({ message: "Registered" }, { status: 201 });
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
