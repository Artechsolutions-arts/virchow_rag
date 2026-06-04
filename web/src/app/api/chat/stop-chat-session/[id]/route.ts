import { NextRequest, NextResponse } from "next/server";

// Stream abort is handled client-side via AbortController.
// This endpoint acknowledges the stop signal for protocol compatibility.
export async function POST(_request: NextRequest) {
  return NextResponse.json({ ok: true });
}
