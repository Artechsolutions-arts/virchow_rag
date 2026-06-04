import { redirect } from "next/navigation";
import { requireAuth } from "@/lib/auth/requireAuth";
import type { Route } from "next";

export default async function ChatLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const authResult = await requireAuth();
  if (authResult.redirect) {
    redirect(authResult.redirect as Route);
  }
  return <>{children}</>;
}
