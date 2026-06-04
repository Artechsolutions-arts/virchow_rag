import { NextResponse } from "next/server";
export async function GET() {
  return NextResponse.json({
    chat_page_enabled: true, search_page_enabled: true,
    default_page: "chat", maximum_chat_retention_days: null,
    notifications: [], needs_reindexing: false,
  });
}
