import type { WidgetDef } from "@/lib/types";
import { TokenDashboard } from "./TokenDashboard";

/**
 * Widget registry — adding a new dashboard widget means:
 * 1. Create the component
 * 2. Add one entry here
 * That's it. Dashboard.tsx reads this automatically.
 */
export const widgets: WidgetDef[] = [
  { id: "tokens", label: "Token Usage", component: TokenDashboard },
  // Future: { id: "vault", label: "Vault", component: VaultDashboard },
  // Future: { id: "agents", label: "Agents", component: AgentDashboard },
];
