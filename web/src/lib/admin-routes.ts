import { IconFunctionComponent } from "@opal/types";
import {
  SvgActions,
  SvgActivity,
  SvgArrowExchange,
  SvgAudio,
  SvgBarChart,
  SvgBookOpen,
  SvgBubbleText,
  SvgClipboard,
  SvgCpu,
  SvgDiscordMono,
  SvgDownload,
  SvgEmpty,
  SvgFileText,
  SvgFiles,
  SvgGlobe,
  SvgHistory,
  SvgImage,
  SvgMcp,
  SvgNetworkGraph,
  SvgVirchowOctagon,
  SvgPaintBrush,
  SvgProgressBars,
  SvgSearchMenu,
  SvgTerminal,
  SvgThumbsUp,
  SvgUploadCloud,
  SvgUser,
  SvgUserKey,
  SvgUserSync,
  SvgUsers,
  SvgWallet,
  SvgZoomIn,
} from "@opal/icons";

export interface AdminRouteEntry {
  path: string;
  icon: IconFunctionComponent;
  title: string;
  sidebarLabel: string;
}

/**
 * Single source of truth for every admin route: path, icon, page-header
 * title, and sidebar label.
 */
export const ADMIN_ROUTES = {
  INDEXING_STATUS: {
    path: "/admin/indexing/status",
    icon: SvgBookOpen,
    title: "Existing Connectors",
    sidebarLabel: "Existing Connectors",
  },
  ADD_CONNECTOR: {
    path: "/admin/add-connector",
    icon: SvgUploadCloud,
    title: "Add Connector",
    sidebarLabel: "Add Connector",
  },
  UPLOAD_FILES: {
    path: "/admin/documents/upload",
    icon: SvgUploadCloud,
    title: "Upload Files",
    sidebarLabel: "Upload Files",
  },
  DOCUMENT_SETS: {
    path: "/admin/documents/sets",
    icon: SvgFiles,
    title: "Document Sets",
    sidebarLabel: "Document Sets",
  },
  DOCUMENT_EXPLORER: {
    path: "/admin/documents/explorer",
    icon: SvgZoomIn,
    title: "Document Explorer",
    sidebarLabel: "Explorer",
  },
  DOCUMENT_FEEDBACK: {
    path: "/admin/documents/feedback",
    icon: SvgThumbsUp,
    title: "Document Feedback",
    sidebarLabel: "Feedback",
  },
  AGENTS: {
    path: "/admin/agents",
    icon: SvgVirchowOctagon,
    title: "Agents",
    sidebarLabel: "Agents",
  },
  OPENAPI_ACTIONS: {
    path: "/admin/actions/open-api",
    icon: SvgActions,
    title: "OpenAPI Actions",
    sidebarLabel: "OpenAPI Actions",
  },
  MCP_ACTIONS: {
    path: "/admin/actions/mcp",
    icon: SvgMcp,
    title: "MCP Servers",
    sidebarLabel: "MCP Servers",
  },
  GROUPS: {
    path: "/admin/groups",
    icon: SvgUsers,
    title: "Manage User Groups",
    sidebarLabel: "Groups",
  },
  STANDARD_ANSWERS: {
    path: "/admin/standard-answer",
    icon: SvgClipboard,
    title: "Standard Answers",
    sidebarLabel: "Standard Answers",
  },
  DISCORD_BOT: {
    path: "/admin/discord-bot",
    icon: SvgDiscordMono,
    title: "Discord Bot",
    sidebarLabel: "Discord Bot",
  },
  CHAT_PREFERENCES: {
    path: "/admin/configuration/chat-preferences",
    icon: SvgBubbleText,
    title: "Chat Preferences",
    sidebarLabel: "Chat Preferences",
  },
  LLM_MODELS: {
    path: "/admin/configuration/llm",
    icon: SvgCpu,
    title: "Language Models",
    sidebarLabel: "Language Models",
  },
  INDEX_SETTINGS: {
    path: "/admin/configuration/search",
    icon: SvgSearchMenu,
    title: "Index Settings",
    sidebarLabel: "Index Settings",
  },
  DOCUMENT_PROCESSING: {
    path: "/admin/configuration/document-processing",
    icon: SvgFileText,
    title: "Document Processing",
    sidebarLabel: "Document Processing",
  },
  USERS: {
    path: "/admin/users",
    icon: SvgUser,
    title: "Users & Requests",
    sidebarLabel: "Users",
  },
  USAGE: {
    path: "/admin/performance/usage",
    icon: SvgActivity,
    title: "Usage Statistics",
    sidebarLabel: "Usage Statistics",
  },
  QUERY_HISTORY: {
    path: "/admin/performance/query-history",
    icon: SvgHistory,
    title: "Users History",
    sidebarLabel: "Users History",
  },
  CUSTOM_ANALYTICS: {
    path: "/admin/performance/custom-analytics",
    icon: SvgBarChart,
    title: "Custom Analytics",
    sidebarLabel: "Custom Analytics",
  },
  THEME: {
    path: "/admin/theme",
    icon: SvgPaintBrush,
    title: "Appearance & Theming",
    sidebarLabel: "Appearance & Theming",
  },
  BILLING: {
    path: "/admin/billing",
    icon: SvgWallet,
    title: "Plans & Billing",
    sidebarLabel: "Plans & Billing",
  },
  // Prefix-only entries used for layout matching — not rendered as sidebar
  // items or page headers.
  DOCUMENTS: {
    path: "/admin/documents",
    icon: SvgEmpty,
    title: "",
    sidebarLabel: "",
  },
  PERFORMANCE: {
    path: "/admin/performance",
    icon: SvgEmpty,
    title: "",
    sidebarLabel: "",
  },
} as const satisfies Record<string, AdminRouteEntry>;

/**
 * Helper that converts a route entry into the `{ name, icon, link }`
 * shape expected by the sidebar.
 */
export function sidebarItem(route: AdminRouteEntry) {
  return { name: route.sidebarLabel, icon: route.icon, link: route.path };
}
