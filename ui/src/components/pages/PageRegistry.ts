import type { ComponentType } from "react";
import {
  IconDashboard,
  IconTokens,
  IconVault,
  IconAgents,
  IconSkills,
  IconModels,
  IconTasks,
  IconCalendar,
  IconMemory,
  IconEvolution,
  IconSettings,
} from "@/components/icons/NavIcons";
import { DashboardHome } from "./DashboardHome";
import { TokenDashboard } from "./TokenDashboard";
import { VaultExplorer } from "./VaultExplorer";
import { AgentTeam } from "./AgentTeam";
import { SkillsExplorer } from "./SkillsExplorer";
import { ModelsView } from "./ModelsView";
import { TasksBoard } from "./TasksBoard";
import { CalendarView } from "./CalendarView";
import { MemoryView } from "./MemoryView";
import { EvolutionView } from "./EvolutionView";
import { SettingsView } from "./SettingsView";

export interface PageDef {
  id: string;
  label: string;
  icon: ComponentType<{ size?: number; className?: string }>;
  component: ComponentType;
  section?: "main" | "system";  // visual grouping in sidebar
}

/**
 * Page registry — adding a new page means:
 * 1. Create the component in this directory
 * 2. Add one entry here
 * Sidebar and PageContent read this automatically.
 */
export const pages: PageDef[] = [
  // ── Main pages ──
  { id: "dashboard", label: "Dashboard", icon: IconDashboard, component: DashboardHome, section: "main" },
  { id: "tokens", label: "Token Usage", icon: IconTokens, component: TokenDashboard, section: "main" },
  { id: "vault", label: "Vault Explorer", icon: IconVault, component: VaultExplorer, section: "main" },
  { id: "agents", label: "Agents", icon: IconAgents, component: AgentTeam, section: "main" },
  { id: "skills", label: "Skills", icon: IconSkills, component: SkillsExplorer, section: "main" },
  { id: "models", label: "Models", icon: IconModels, component: ModelsView, section: "main" },
  { id: "tasks", label: "Tasks", icon: IconTasks, component: TasksBoard, section: "main" },
  { id: "calendar", label: "Calendar", icon: IconCalendar, component: CalendarView, section: "main" },
  { id: "memory", label: "Memory", icon: IconMemory, component: MemoryView, section: "main" },
  { id: "evolution", label: "Field State", icon: IconEvolution, component: EvolutionView, section: "main" },
  // ── System ──
  { id: "settings", label: "Settings", icon: IconSettings, component: SettingsView, section: "system" },
];
