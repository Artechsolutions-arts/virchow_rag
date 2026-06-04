import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

function ndjson(...objs: object[]): string {
  return objs.map((o) => JSON.stringify(o)).join("\n") + "\n";
}

export async function POST(request: NextRequest) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const body = await request.json().catch(() => ({}));
  const question: string = body.message ?? "";
  const chatSessionId: string | null = body.chat_session_id || null;

  // Call our RAG backend (non-streaming JSON)
  let answer = "Sorry, I encountered an error processing your request.";
  let returnedChatId = chatSessionId;

  try {
    const res = await fetch(`${INTERNAL_URL}/query`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ question, chat_id: chatSessionId }),
      signal: AbortSignal.timeout(130_000),
    });

    if (res.ok) {
      const data = await res.json();
      answer = data.answer ?? answer;
      returnedChatId = data.chat_id ?? chatSessionId;

      // For single-source answers: strip the leading **filename.ext** prefix
      // (stored in DB for conversation continuity but shouldn't be the first thing shown)
      // Multi-source answers keep filename headers so users see which doc each answer is from
      const singleSourcePrefix = /^\*\*[^*]+\.(pdf|xlsx?|docx?|csv|txt)\*\*\n/;
      const multiSourcePattern = /\n\n\*\*[^*]+\.(pdf|xlsx?|docx?|csv|txt)\*\*\n/;
      if (singleSourcePrefix.test(answer) && !multiSourcePattern.test(answer)) {
        answer = answer.replace(singleSourcePrefix, "").trim();
      }

      // Append citation links as markdown
      const citations: Array<{ name: string; document_id: string; url: string }> =
        data.citations ?? [];
      if (citations.length > 0) {
        const sourceLines = citations
          .map((c) => `- [${c.name}](${c.url ?? "#"})`)
          .join("\n");
        answer = `${answer}\n\n**Sources:**\n${sourceLines}`;
      }
    }
  } catch {
    // answer stays as error message
  }

  // Stream back newline-delimited JSON packets the AppPage expects
  const stream = new ReadableStream({
    start(controller) {
      const enc = new TextEncoder();

      // 1. Message IDs
      controller.enqueue(
        enc.encode(
          ndjson({ user_message_id: null, reserved_assistant_message_id: 1 })
        )
      );

      // 2. Full answer in message_start
      controller.enqueue(
        enc.encode(
          ndjson({
            placement: { turn_index: 0, tab_index: 0 },
            obj: {
              type: "message_start",
              id: "1",
              content: answer,
              final_documents: [],
              pre_answer_processing_seconds: 0,
            },
          })
        )
      );

      // 3. Stop — processor auto-injects section_end
      controller.enqueue(
        enc.encode(
          ndjson({
            placement: { turn_index: 0, tab_index: 0 },
            obj: { type: "stop", stop_reason: "finished" },
          })
        )
      );

      controller.close();
    },
  });

  return new NextResponse(stream, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Transfer-Encoding": "chunked",
      "X-Chat-Session-Id": returnedChatId ?? "",
    },
  });
}
