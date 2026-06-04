import { NextResponse } from "next/server";
export async function PATCH() {
  return NextResponse.json({ ok: true });
}
export async function GET() {
  return NextResponse.json({});
}
