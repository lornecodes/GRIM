"use client";

import { useGrimStore } from "@/store";
import { pages } from "./pages/PageRegistry";
import { IconChevronLeft, IconChevronRight } from "./icons/NavIcons";

export function Sidebar() {
  const activePage = useGrimStore((s) => s.activePage);
  const setActivePage = useGrimStore((s) => s.setActivePage);
  const collapsed = useGrimStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useGrimStore((s) => s.toggleSidebar);

  const mainPages = pages.filter((p) => p.section !== "system");
  const systemPages = pages.filter((p) => p.section === "system");

  return (
    <nav
      className="flex flex-col shrink-0 bg-grim-surface border-r border-grim-border transition-[width] duration-200 overflow-hidden"
      style={{ width: collapsed ? 48 : 200 }}
    >
      {/* Main nav items */}
      <div className="flex-1 flex flex-col gap-0.5 pt-2 px-1.5">
        {mainPages.map((page) => {
          const Icon = page.icon;
          const isActive = page.id === activePage;
          return (
            <button
              key={page.id}
              onClick={() => setActivePage(page.id)}
              title={collapsed ? page.label : undefined}
              className={`flex items-center gap-2.5 rounded-md transition-all text-left ${
                collapsed ? "justify-center px-0 py-2" : "px-2.5 py-2"
              } ${
                isActive
                  ? "bg-grim-accent/10 text-grim-accent"
                  : "text-grim-text-dim hover:text-grim-text hover:bg-grim-surface-hover"
              }`}
            >
              <Icon size={16} className="shrink-0" />
              {!collapsed && (
                <span className="text-[12px] truncate">{page.label}</span>
              )}
            </button>
          );
        })}
      </div>

      {/* Divider + system pages */}
      <div className="flex flex-col gap-0.5 px-1.5 pb-1">
        <div className="border-t border-grim-border my-1" />
        {systemPages.map((page) => {
          const Icon = page.icon;
          const isActive = page.id === activePage;
          return (
            <button
              key={page.id}
              onClick={() => setActivePage(page.id)}
              title={collapsed ? page.label : undefined}
              className={`flex items-center gap-2.5 rounded-md transition-all text-left ${
                collapsed ? "justify-center px-0 py-2" : "px-2.5 py-2"
              } ${
                isActive
                  ? "bg-grim-accent/10 text-grim-accent"
                  : "text-grim-text-dim hover:text-grim-text hover:bg-grim-surface-hover"
              }`}
            >
              <Icon size={16} className="shrink-0" />
              {!collapsed && (
                <span className="text-[12px] truncate">{page.label}</span>
              )}
            </button>
          );
        })}

        {/* Collapse toggle */}
        <button
          onClick={toggleSidebar}
          className={`flex items-center gap-2.5 rounded-md text-grim-text-dim hover:text-grim-text hover:bg-grim-surface-hover transition-all ${
            collapsed ? "justify-center px-0 py-2" : "px-2.5 py-2"
          }`}
        >
          {collapsed ? <IconChevronRight size={14} /> : <IconChevronLeft size={14} />}
          {!collapsed && (
            <span className="text-[11px]">Collapse</span>
          )}
        </button>
      </div>
    </nav>
  );
}
