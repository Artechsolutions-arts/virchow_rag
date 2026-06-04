import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

export async function GET(
  request: NextRequest,
  props: { params: Promise<{ id: string }> }
) {
  const { id } = await props.params;
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) {
    return NextResponse.json([], { status: 200 });
  }

  try {
    const res = await fetch(`${INTERNAL_URL}/chats/${id}/messages`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (e) {
    return NextResponse.json([], { status: 200 });
  }
}
