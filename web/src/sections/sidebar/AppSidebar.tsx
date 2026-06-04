"use client";

import { useCallback, memo, useMemo, useState, useEffect, useRef, useLayoutEffect } from "react";
import useSWR from "swr";
import { useRouter, useSearchParams } from "next/navigation";
import { useSettingsContext } from "@/providers/SettingsProvider";
import { MinimalPersonaSnapshot } from "@/app/admin/agents/interfaces";
import Text from "@/refresh-components/texts/Text";
import ChatButton from "@/sections/sidebar/ChatButton";
import AgentButton from "@/sections/sidebar/AgentButton";
import { DragEndEvent } from "@dnd-kit/core";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  pointerWithin,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { useDroppable } from "@dnd-kit/core";
import {
  restrictToFirstScrollableAncestor,
  restrictToVerticalAxis,
} from "@dnd-kit/modifiers";
import SidebarSection from "@/sections/sidebar/SidebarSection";
import useChatSessions from "@/hooks/useChatSessions";
import { useProjects } from "@/lib/hooks/useProjects";
import { useAgents, useCurrentAgent, usePinnedAgents } from "@/hooks/useAgents";
import { useAppSidebarContext } from "@/providers/AppSidebarProvider";
import ProjectFolderButton from "@/sections/sidebar/ProjectFolderButton";
import SidebarWrapper from "@/sections/sidebar/SidebarWrapper";
import { Button as OpalButton } from "@opal/components";
import { cn } from "@/lib/utils";
import {
  DRAG_TYPES,
  DEFAULT_PERSONA_ID,
  FEATURE_FLAGS,
  LOCAL_STORAGE_KEYS,
} from "@/sections/sidebar/constants";
import { showErrorNotification, handleMoveOperation } from "./sidebarUtils";
import SidebarTab from "@/refresh-components/buttons/SidebarTab";
import { ChatSession } from "@/app/app/interfaces";
import SidebarBody from "@/sections/sidebar/SidebarBody";
import { useUser } from "@/providers/UserProvider";
import useAppFocus from "@/hooks/useAppFocus";
import { useCreateModal } from "@/refresh-components/contexts/ModalContext";
import { useModalContext } from "@/components/context/ModalContext";
import useScreenSize from "@/hooks/useScreenSize";
import {
  SvgEditBig,
  SvgFolderPlus,
  SvgSettings,
  SvgUploadCloud,
} from "@opal/icons";
import SidebarTabSkeleton from "@/refresh-components/skeletons/SidebarTabSkeleton";
import BuildModeIntroBackground from "@/app/craft/components/IntroBackground";
import BuildModeIntroContent from "@/app/craft/components/IntroContent";
import { CRAFT_PATH } from "@/app/craft/v1/constants";
import { usePostHog } from "posthog-js/react";
import { motion, AnimatePresence } from "motion/react";
import { Notification, NotificationType } from "@/interfaces/settings";
import { errorHandlingFetcher } from "@/lib/fetcher";
import UserAvatarPopover from "@/sections/sidebar/UserAvatarPopover";
import { useQueryController } from "@/providers/QueryControllerProvider";

// Visible-agents = pinned-agents + current-agent (if current-agent not in pinned-agents)
// OR Visible-agents = pinned-agents (if current-agent in pinned-agents)
function buildVisibleAgents(
  pinnedAgents: MinimalPersonaSnapshot[],
  currentAgent: MinimalPersonaSnapshot | null
): [MinimalPersonaSnapshot[], boolean] {
  /* NOTE: The unified agent (id = 0) is not visible in the sidebar,
  so we filter it out. */
  if (!currentAgent)
    return [pinnedAgents.filter((agent) => agent.id !== 0), false];
  const currentAgentIsPinned = pinnedAgents.some(
    (pinnedAgent) => pinnedAgent.id === currentAgent.id
  );
  const visibleAgents = (
    currentAgentIsPinned ? pinnedAgents : [...pinnedAgents, currentAgent]
  ).filter((agent) => agent.id !== 0);

  return [visibleAgents, currentAgentIsPinned];
}

const SKELETON_WIDTHS_BASE = ["w-4/5", "w-4/5", "w-3/5"];

function shuffleWidths(): string[] {
  return [...SKELETON_WIDTHS_BASE].sort(() => Math.random() - 0.5);
}

interface RecentsSectionProps {
  chatSessions: ChatSession[];
  hasMore: boolean;
  isLoadingMore: boolean;
  onLoadMore: () => void;
}

function RecentsSection({
  chatSessions,
  hasMore,
  isLoadingMore,
  onLoadMore,
}: RecentsSectionProps) {
  const { setNodeRef, isOver } = useDroppable({
    id: DRAG_TYPES.RECENTS,
    data: {
      type: DRAG_TYPES.RECENTS,
    },
  });

  // Re-shuffle skeleton widths each time loaded session count changes
  const skeletonWidths = useMemo(shuffleWidths, [chatSessions.length]);

  // Sentinel ref for IntersectionObserver-based infinite scroll
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const onLoadMoreRef = useRef(onLoadMore);
  onLoadMoreRef.current = onLoadMore;

  useEffect(() => {
    if (!hasMore || isLoadingMore) return;

    const sentinel = sentinelRef.current;
    if (!sentinel) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          onLoadMoreRef.current();
        }
      },
      { threshold: 0 }
    );

    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasMore, isLoadingMore]);

  return (
    <div
      ref={setNodeRef}
      className={cn(
        "transition-colors duration-200 rounded-08 h-full",
        isOver && "bg-background-tint-03"
      )}
    >
      <SidebarSection title="Recents">
        {chatSessions.length === 0 ? (
          <Text as="p" text01 className="px-3">
            Try sending a message! Your chat history will appear here.
          </Text>
        ) : (
          <>
            {chatSessions.map((chatSession) => (
              <ChatButton
                key={chatSession.id}
                chatSession={chatSession}
                draggable
              />
            ))}
            {hasMore &&
              skeletonWidths.map((width, i) => (
                <div
                  key={i}
                  ref={i === 0 ? sentinelRef : undefined}
                  className={cn(
                    "transition-opacity duration-300",
                    isLoadingMore ? "opacity-100" : "opacity-40"
                  )}
                >
                  <SidebarTabSkeleton textWidth={width} />
                </div>
              ))}
          </>
        )}
      </SidebarSection>
    </div>
  );
}

interface AppSidebarInnerProps {
  folded: boolean;
  onFoldClick: () => void;
}

const MemoizedAppSidebarInner = memo(
  ({ folded, onFoldClick }: AppSidebarInnerProps) => {
    const router = useRouter();
    const searchParams = useSearchParams();
    const combinedSettings = useSettingsContext();
    const posthog = usePostHog();
    const { newTenantInfo, invitationInfo } = useModalContext();
    const { setAppMode, reset } = useQueryController();

    // Use SWR hooks for data fetching
    const {
      chatSessions,
      refreshChatSessions,
      isLoading: isLoadingChatSessions,
      hasMore,
      isLoadingMore,
      loadMore,
    } = useChatSessions();
    const {
      projects,
      refreshProjects,
      isLoading: isLoadingProjects,
    } = useProjects();
    const { isLoading: isLoadingAgents } = useAgents();
    const currentAgent = useCurrentAgent();
    const {
      pinnedAgents,
      updatePinnedAgents,
      isLoading: isLoadingPinnedAgents,
    } = usePinnedAgents();

    // Wait for ALL dynamic data before showing any sections
    const isLoadingDynamicContent =
      isLoadingChatSessions ||
      isLoadingProjects ||
      isLoadingAgents ||
      isLoadingPinnedAgents;



    // State for custom agent modal
    const [pendingMoveChatSession, setPendingMoveChatSession] =
      useState<ChatSession | null>(null);
    const [pendingMoveProjectId, setPendingMoveProjectId] = useState<
      number | null
    >(null);
    const [showMoveCustomAgentModal, setShowMoveCustomAgentModal] =
      useState(false);

    // Fetch notifications for build mode intro
    const { data: notifications, mutate: mutateNotifications } = useSWR<
      Notification[]
    >("/api/notifications", errorHandlingFetcher);

    // Check if Virchow Craft is enabled via settings (backed by PostHog feature flag)
    // Only explicit true enables the feature; false or undefined = disabled
    // Force Virchow Craft to be disabled
    const isVirchowCraftEnabled = false;

    // Find build_mode feature announcement notification (only if Virchow Craft is enabled)
    const buildModeNotification = isVirchowCraftEnabled
      ? notifications?.find(
        (n) =>
          n.notif_type === NotificationType.FEATURE_ANNOUNCEMENT &&
          n.additional_data?.feature === "build_mode" &&
          !n.dismissed
      )
      : undefined;

    // State for intro animation overlay
    const [showIntroAnimation, setShowIntroAnimation] = useState(false);
    // Track if auto-trigger has fired (prevents race condition during dismiss)
    const hasAutoTriggeredRef = useRef(false);

    // Auto-show intro once when there's an undismissed notification
    // Don't show if tenant/invitation modal is open (e.g., "join existing team" modal)
    // Gated by PostHog feature flag: if `craft-animation-disabled` is true (or
    // PostHog is unavailable), skip the auto-show entirely.
    const isCraftAnimationDisabled =
      posthog?.isFeatureEnabled(FEATURE_FLAGS.CRAFT_ANIMATION_DISABLED) ?? true;
    const hasTenantModal = !!(newTenantInfo || invitationInfo);
    useEffect(() => {
      if (
        isVirchowCraftEnabled &&
        buildModeNotification &&
        !hasAutoTriggeredRef.current &&
        !hasTenantModal &&
        !isCraftAnimationDisabled
      ) {
        hasAutoTriggeredRef.current = true;
        setShowIntroAnimation(true);
      }
    }, [
      buildModeNotification,
      isVirchowCraftEnabled,
      hasTenantModal,
      isCraftAnimationDisabled,
    ]);

    // Dismiss the build mode notification
    const dismissBuildModeNotification = useCallback(async () => {
      if (!buildModeNotification) return;
      try {
        await fetch(`/api/notifications/${buildModeNotification.id}/dismiss`, {
          method: "POST",
        });
        mutateNotifications();
      } catch (error) {
        console.error("Error dismissing notification:", error);
      }
    }, [buildModeNotification, mutateNotifications]);

    const [visibleAgents, currentAgentIsPinned] = useMemo(
      () => buildVisibleAgents(pinnedAgents, currentAgent),
      [pinnedAgents, currentAgent]
    );
    const visibleAgentIds = useMemo(
      () => visibleAgents.map((agent) => agent.id),
      [visibleAgents]
    );

    const sensors = useSensors(
      useSensor(PointerSensor, {
        activationConstraint: {
          distance: 8,
        },
      }),
      useSensor(KeyboardSensor, {
        coordinateGetter: sortableKeyboardCoordinates,
      })
    );

    // Handle agent drag and drop
    const handleAgentDragEnd = useCallback(
      (event: DragEndEvent) => {
        const { active, over } = event;
        if (!over) return;
        if (active.id === over.id) return;

        const activeIndex = visibleAgentIds.findIndex(
          (agentId) => agentId === active.id
        );
        const overIndex = visibleAgentIds.findIndex(
          (agentId) => agentId === over.id
        );

        let newPinnedAgents: MinimalPersonaSnapshot[];

        if (currentAgent && !currentAgentIsPinned) {
          // This is the case in which the user is dragging the UNPINNED agent and moving it to somewhere else in the list.
          // This is an indication that we WANT to pin this agent!
          if (activeIndex === visibleAgentIds.length - 1) {
            const pinnedWithCurrent = [...pinnedAgents, currentAgent];
            newPinnedAgents = arrayMove(
              pinnedWithCurrent,
              activeIndex,
              overIndex
            );
          } else {
            // Use visibleAgents to ensure the indices match with `visibleAgentIds`
            newPinnedAgents = arrayMove(visibleAgents, activeIndex, overIndex);
          }
        } else {
          // Use visibleAgents to ensure the indices match with `visibleAgentIds`
          newPinnedAgents = arrayMove(visibleAgents, activeIndex, overIndex);
        }

        updatePinnedAgents(newPinnedAgents);
      },
      [
        visibleAgentIds,
        visibleAgents,
        pinnedAgents,
        updatePinnedAgents,
        currentAgent,
        currentAgentIsPinned,
      ]
    );



    const { isAdmin, isCurator, user } = useUser();
    const activeSidebarTab = useAppFocus();
    const defaultAppMode =
      (user?.preferences?.default_app_mode?.toLowerCase() as
        | "chat"
        | "search") ?? "chat";
    const newSessionButton = useMemo(() => {
      const href =
        combinedSettings?.settings?.disable_default_assistant && currentAgent
          ? `/app?agentId=${currentAgent.id}`
          : "/app";
      return (
        <div data-testid="AppSidebar/new-session">
          <SidebarTab
            icon={SvgEditBig}
            folded={folded}
            href={href}
            selected={activeSidebarTab.isNewSession()}
            onClick={() => {
              if (!activeSidebarTab.isNewSession()) return;
              setAppMode(defaultAppMode);
              reset();
            }}
          >
            New Session
          </SidebarTab>
        </div>
      );
    }, [
      folded,
      activeSidebarTab,
      combinedSettings,
      currentAgent,
      defaultAppMode,
    ]);

    const handleShowBuildIntro = useCallback(() => {
      setShowIntroAnimation(true);
    }, []);

    const settingsButton = useMemo(
      () => (
        <div>
          {isAdmin && (
            <SidebarTab
              href="/admin/configuration/llm"
              icon={SvgSettings}
              folded={folded}
            >
              Admin Panel
            </SidebarTab>
          )}
          <UserAvatarPopover
            folded={folded}
            onShowBuildIntro={
              isVirchowCraftEnabled ? handleShowBuildIntro : undefined
            }
          />
        </div>
      ),
      [folded, isAdmin, isCurator, handleShowBuildIntro, isVirchowCraftEnabled]
    );

    return (
      <>




        {/* Intro animation overlay */}
        <AnimatePresence>
          {showIntroAnimation && (
            <motion.div
              className="fixed inset-0 z-[9999]"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.5 }}
            >
              <BuildModeIntroBackground />
              <BuildModeIntroContent
                onClose={() => {
                  setShowIntroAnimation(false);
                  dismissBuildModeNotification();
                }}
                onTryBuildMode={() => {
                  setShowIntroAnimation(false);
                  dismissBuildModeNotification();
                  router.push(CRAFT_PATH);
                }}
              />
            </motion.div>
          )}
        </AnimatePresence>

        <SidebarWrapper folded={folded} onFoldClick={onFoldClick}>
          <SidebarBody
            scrollKey="app-sidebar"
            footer={settingsButton}
            actionButtons={
              <div className="flex flex-col gap-[10px]">
                {newSessionButton}
                <SidebarTab
                  icon={SvgUploadCloud}
                  folded={folded}
                  href="/app?view=upload"
                  selected={searchParams?.get("view") === "upload"}
                >
                  Upload Files
                </SidebarTab>
              </div>
            }
          >
            {/* When folded, show icons immediately without waiting for data */}
            {folded ? (
              <></>
            ) : isLoadingDynamicContent ? null : (
              <>
                <RecentsSection
                  chatSessions={chatSessions}
                  hasMore={hasMore}
                  isLoadingMore={isLoadingMore}
                  onLoadMore={loadMore}
                />
              </>
            )}
          </SidebarBody>
        </SidebarWrapper>
      </>
    );
  }
);
MemoizedAppSidebarInner.displayName = "AppSidebar";

export default function AppSidebar() {
  const { folded, setFolded } = useAppSidebarContext();
  const { isMobile } = useScreenSize();

  // On mobile viewports, start with the sidebar closed so it doesn't overlay content.
  // useLayoutEffect fires synchronously before paint to avoid a visible flash.
  useLayoutEffect(() => {
    if (isMobile && !folded) {
      setFolded(true);
    }
  }, [isMobile]);

  if (!isMobile)
    return (
      <MemoizedAppSidebarInner
        folded={folded}
        onFoldClick={() => setFolded((prev) => !prev)}
      />
    );

  return (
    <>
      <div
        className={cn(
          "fixed inset-y-0 left-0 z-50 transition-transform duration-200",
          folded ? "-translate-x-full" : "translate-x-0"
        )}
      >
        <MemoizedAppSidebarInner
          folded={false}
          onFoldClick={() => setFolded(true)}
        />
      </div>

      {/* Hitbox to close the sidebar if anything outside of it is touched */}
      <div
        className={cn(
          "fixed inset-0 z-40 bg-mask-03 backdrop-blur-03 transition-opacity duration-200",
          folded
            ? "opacity-0 pointer-events-none"
            : "opacity-100 pointer-events-auto"
        )}
        onClick={() => setFolded(true)}
      />
    </>
  );
}
