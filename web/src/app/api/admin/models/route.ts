import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

// GET /api/admin/models — current LLM + embedding + available Ollama models
// POST /api/admin/models/llm — switch the active LLM model

export async function GET(request: NextRequest) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) {
    return NextResponse.json({ detail: "Unauthorized" }, { status: 401 });
  }
  const res = await fetch(`${INTERNAL_URL}/admin/models`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const data = await res.json().catch(() => ({ detail: "Bad response" }));
  return NextResponse.json(data, { status: res.status });
}
