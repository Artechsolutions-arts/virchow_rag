import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

export async function GET(
  _request: NextRequest,
  context: { params: Promise<{ docId: string }> }
) {
  const { docId } = await context.params;

  try {
    // Fetch from backend which 302-redirects to SeaweedFS; follow the redirect
    const res = await fetch(`${INTERNAL_URL}/api/documents/${docId}`, {
      redirect: "follow",
    });

    if (!res.ok) {
      return NextResponse.json({ error: "Document not found" }, { status: 404 });
    }

    const contentType =
      res.headers.get("Content-Type") || "application/octet-stream";
    const disposition =
      res.headers.get("Content-Disposition") ||
      `inline; filename="${docId}"`;

    return new Response(res.body, {
      status: 200,
      headers: {
        "Content-Type": contentType,
        "Content-Disposition": disposition,
      },
    });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      { error: `Failed to load document: ${detail}` },
      { status: 500 }
    );
  }
}
