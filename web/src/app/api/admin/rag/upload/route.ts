import { NextRequest, NextResponse } from "next/server";
import { INTERNAL_URL } from "@/lib/constants";

export async function POST(request: NextRequest) {
  const token = request.cookies.get("fastapiusersauth")?.value;
  if (!token) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    const incomingForm = await request.formData();
    // Frontend sends multiple "files" entries; backend accepts one "file" at a time.
    const allFiles = incomingForm.getAll("files");
    if (allFiles.length === 0) {
      return NextResponse.json({ error: "No files provided" }, { status: 422 });
    }

    const CONCURRENCY = 10;

    const uploadOne = async (fileEntry: FormDataEntryValue) => {
      const proxyForm = new FormData();
      proxyForm.append("file", fileEntry);
      const res = await fetch(`${INTERNAL_URL}/documents/upload`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: proxyForm,
      });
      return { data: await res.json().catch(() => ({})), status: res.status };
    };

    const results: unknown[] = [];
    for (let i = 0; i < allFiles.length; i += CONCURRENCY) {
      const wave = await Promise.all(allFiles.slice(i, i + CONCURRENCY).map(uploadOne));
      results.push(...wave.map((r) => r.data));
    }

    return NextResponse.json({ results }, { status: 202 });
  } catch (e) {
    const message = e instanceof Error ? e.message : "Unknown error";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
