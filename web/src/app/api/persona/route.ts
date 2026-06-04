import { NextResponse } from "next/server";

const DEFAULT_PERSONA = {
  id: 0,
  name: "Virchow Assistant",
  description: "Your RAG-powered knowledge assistant",
  tools: [],
  starter_messages: null,
  document_sets: [],
  is_public: true,
  is_visible: true,
  display_priority: 0,
  featured: true,
  builtin_persona: true,
  owner: null,
  labels: [],
  uploaded_image_id: null,
  icon_name: null,
  llm_model_version_override: null,
  llm_model_provider_override: null,
};

export async function GET() {
  return NextResponse.json([DEFAULT_PERSONA]);
}
