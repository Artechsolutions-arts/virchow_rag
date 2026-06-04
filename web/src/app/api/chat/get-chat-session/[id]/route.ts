import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

export async function GET(
  request: NextRequest,
  props: { params: Promise<{ id: string }> }
) {
  const { id } = await props.params;
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  try {
    const [msgsRes, metaRes] = await Promise.all([
      fetch(`${INTERNAL_URL}/chats/${id}/messages`, {
        headers: { Authorization: `Bearer ${token}` },
      }),
      fetch(`${INTERNAL_URL}/chats/${id}/meta`, {
        headers: { Authorization: `Bearer ${token}` },
      }),
    ]);

    const rawMsgs: any[] = msgsRes.ok ? await msgsRes.json() : [];
    const meta: any = metaRes.ok ? await metaRes.json() : {};

    // For single-source answers: strip the leading **filename.ext** prefix
    // (stored for context tracking but shouldn't show as the first line in the chat)
    const stripFilenamePrefix = (content: string) => {
      const singleSourcePrefix = /^\*\*[^*]+\.(pdf|xlsx?|docx?|csv|txt)\*\*\n/;
      const multiSourcePattern = /\n\n\*\*[^*]+\.(pdf|xlsx?|docx?|csv|txt)\*\*\n/;
      if (singleSourcePrefix.test(content) && !multiSourcePattern.test(content)) {
        return content.replace(singleSourcePrefix, "").trim();
      }
      return content;
    };

    // Build parent-child linked list
    const messages = rawMsgs.map((m: any, idx: number) => ({
      message_id: idx + 1,
      message_type: m.role === "user" ? "user" : "assistant",
      research_type: null,
      parent_message: idx > 0 ? idx : null,
      latest_child_message: idx < rawMsgs.length - 1 ? idx + 2 : null,
      message: m.role === "assistant" ? stripFilenamePrefix(m.content) : m.content,
      rephrased_query: null,
      context_docs: null,
      time_sent: m.created_at,
      overridden_model: "",
      alternate_assistant_id: null,
      chat_session_id: id,
      citations: null,
      files: [],
      tool_call: null,
      current_feedback: null,
      sub_questions: [],
      comments: null,
      parentMessageId: null,
      refined_answer_improvement: null,
      is_agentic: null,
    }));

    const now = new Date().toISOString();
    return NextResponse.json({
      chat_session_id: id,
      description: meta.title || "New Chat",
      persona_id: 0,
      persona_name: "Virchow Assistant",
      messages,
      time_created: meta.created_at ?? now,
      time_updated: meta.updated_at ?? now,
      shared_status: "private",
      current_temperature_override: null,
      current_alternate_model: "",
      owner_name: null,
      packets: [],
    });
  } catch {
    return NextResponse.json({ error: "Backend error" }, { status: 500 });
  }
}
