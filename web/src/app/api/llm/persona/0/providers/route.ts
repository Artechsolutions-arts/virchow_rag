import { NextResponse } from "next/server";

export async function GET() {
  return NextResponse.json({
    providers: [
      {
        id: 1,
        name: "ollama",
        provider: "ollama",
        api_key: null,
        api_base: null,
        api_version: null,
        custom_config: null,
        is_public: true,
        is_auto_mode: true,
        groups: [],
        personas: [],
        deployment_name: null,
        model_configurations: [
          {
            name: "qwen2.5:latest",
            is_visible: true,
            max_input_tokens: null,
            supports_image_input: false,
            supports_reasoning: false,
            display_name: "Qwen 2.5",
          },
        ],
      },
    ],
    default_text: { provider_id: 1, model_name: "qwen2.5:latest" },
    default_vision: null,
  });
}
