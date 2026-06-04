import { redirect } from "next/navigation";
import { unstable_noStore as noStore } from "next/cache";
import { cookies } from "next/headers";
import { ProjectsProvider } from "@/providers/ProjectsContext";
import { VoiceModeProvider } from "@/providers/VoiceModeProvider";
import AppSidebar from "@/sections/sidebar/AppSidebar";

export interface LayoutProps {
  children: React.ReactNode;
}

export default async function Layout({ children }: LayoutProps) {
  noStore();

  // Fast cookie-presence check — full validation happens client-side via UserProvider
  const requestCookies = await cookies();
  if (!requestCookies.has("fastapiusersauth")) {
    redirect("/auth/login");
  }

  return (
    <ProjectsProvider>
      {/* VoiceModeProvider wraps the full app layout so TTS playback state
          persists across page navigations (e.g., sidebar clicks during playback).
          It only activates WebSocket connections when TTS is actually triggered. */}
      <VoiceModeProvider>
        <div className="flex flex-row w-full h-full">
          <AppSidebar />
          {children}
        </div>
      </VoiceModeProvider>
    </ProjectsProvider>
  );
}
