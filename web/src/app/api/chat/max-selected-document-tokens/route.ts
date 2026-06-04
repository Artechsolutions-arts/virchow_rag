import { NextResponse } from "next/server";

export async function GET() {
  return NextResponse.json({ max_tokens: 16000 });
}
