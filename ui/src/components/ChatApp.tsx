"use client";

import { AppHeader } from "./AppHeader";
import { ChatPanel } from "./ChatPanel";
import { Sidebar } from "./Sidebar";
import { PageContent } from "./PageContent";

export function ChatApp() {
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
