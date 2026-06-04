import { NextResponse } from "next/server";

// No invite system in this deployment — return empty list so the UI renders cleanly.
export async function GET() {
  return NextResponse.json([]);
}
