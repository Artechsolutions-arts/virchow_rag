import { NextResponse } from "next/server";
export async function GET() {
  return NextResponse.json({ available_tokens: 128000 });
}
