import { NextResponse } from "next/server";
export async function GET() {
  return NextResponse.json({
    product_gating: null, enable_automatic_model_version_upgrade: false,
    two_factor_enforcement_policy: "NO_ENFORCEMENT",
    auto_scroll_when_sending: false, enable_experimental_features: false,
  });
}
