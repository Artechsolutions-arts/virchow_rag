import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

export async function POST(request: NextRequest) {
  const requestStarted = Date.now();

  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    const body = await request.json().catch(() => ({}));
    const question = ((body.question as string) || "").trim();
    const chatSessionId: string | null = (body.chat_session_id as string) || null;

    if (!question) {
      return NextResponse.json(
        { error: "question is required", duration_ms: 0 },
        { status: 400 }
      );
    }

    const res = await fetch(`${INTERNAL_URL}/query`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ question, chat_id: chatSessionId }),
      signal: AbortSignal.timeout(120_000),
    });

    if (!res.ok) {
      const detail = await res.text().catch(() => res.statusText);
      return NextResponse.json({
        answer: `The knowledge base returned an error (${res.status}): ${detail}`,
        sources: [],
        duration_ms: Date.now() - requestStarted,
      });
    }

    const data = await res.json();
    const citations: Array<{ name: string; document_id: string; url: string }> =
      Array.isArray(data.citations) ? data.citations : [];

    const sources = citations.map((c) => ({
      doc_id: c.document_id,
      source_file: c.name,
      file_id: null as string | null,
      url: c.url || null,
    }));

    return NextResponse.json({
      answer: data.answer ?? "No answer returned.",
      sources,
      duration_ms: Date.now() - requestStarted,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({
      answer: `Could not complete the request: ${message}`,
      sources: [],
      duration_ms: Date.now() - requestStarted,
    });
  }
}
