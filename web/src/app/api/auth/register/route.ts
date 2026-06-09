import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

export async function POST(request: NextRequest) {
  const body = await request.json().catch(() => ({}));
  const password = body.password || "";
  // Username is the primary identity. Falls back to the email local-part
  // for backward compatibility with legacy callers that only sent email.
  const rawEmail = (body.email || "").trim();
  const rawName =
    (body.name || body.username || "").trim() ||
    (rawEmail ? rawEmail.split("@")[0] : "");
  const email = rawEmail || undefined;
  const name = rawName;

  try {
    const res = await fetch(`${INTERNAL_URL}/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // email is optional on the backend now; omit when blank
      body: JSON.stringify({ name, password, ...(email ? { email } : {}) }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      if (res.status === 409) {
        return NextResponse.json(
          { detail: err.detail || "REGISTER_USERNAME_ALREADY_EXISTS" },
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
