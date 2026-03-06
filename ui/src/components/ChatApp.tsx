"use client";

import { AppHeader } from "./AppHeader";
import { ChatPanel } from "./ChatPanel";
import { Sidebar } from "./Sidebar";
import { PageContent } from "./PageContent";
import { usePoolSocket } from "@/hooks/usePoolSocket";

export function ChatApp() {
  // Single pool WebSocket connection for the entire app
  usePoolSocket();

  return (
    <div className="h-screen flex flex-col bg-grim-bg font-mono">
      <AppHeader />
      <div className="flex-1 flex overflow-hidden">
        <Sidebar />
        <PageContent />
        <ChatPanel />
      </div>
    </div>
  );
}
