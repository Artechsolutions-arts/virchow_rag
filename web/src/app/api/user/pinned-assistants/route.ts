import { NextResponse } from "next/server";

// Pinning assistants is not supported by the backend.
// Return 200 so the UI doesn't log errors on every chat load.
export async function PATCH() {
  return NextResponse.json({ ok: true });
}
