"use client";

import { useEffect, useCallback, useState } from "react";
import { ReadonlyURLSearchParams } from "next/navigation";
import {
  processRawChatHistory,
  patchMessageToBeLatest,
} from "@/app/app/services/lib";
import {
  getLatestMessageChain,
  setMessageAsLatest,
} from "@/app/app/services/messageTree";
import {
  BackendChatSession,
  ChatSessionSharedStatus,
} from "@/app/app/interfaces";
import {
  SEARCH_PARAM_NAMES,
  shouldSubmitOnLoad,
} from "@/app/app/services/searchParams";
import { FilterManager } from "@/lib/hooks";
import { VirchowDocument } from "@/lib/search/interfaces";
import {
  useChatSessionStore,
  useCurrentMessageHistory,
} from "@/app/app/stores/useChatSessionStore";
import { useForcedTools } from "@/lib/hooks/useForcedTools";
import { ProjectFile } from "@/app/app/projects/projectsService";
import { getSessionProjectTokenCount } from "@/app/app/projects/projectsService";
import { getProjectFilesForSession } from "@/app/app/projects/projectsService";
import { AppInputBarHandle } from "@/sections/input/AppInputBar";

interface UseChatSessionControllerProps {
  existingChatSessionId: string | null;
  searchParams: ReadonlyURLSearchParams;
  filterManager: FilterManager;
  firstMessage?: string;

  // UI state setters
  setSelectedAgentFromId: (agentId: number | null) => void;
  setSelectedDocuments: (documents: VirchowDocument[]) => void;
  setCurrentMessageFiles: (
    files: ProjectFile[] | ((prev: ProjectFile[]) => ProjectFile[])
  ) => void;

  // Refs
  chatSessionIdRef: React.RefObject<string | null>;
  loadedIdSessionRef: React.RefObject<string | null>;
  chatInputBarRef: React.RefObject<AppInputBarHandle | null>;
  isInitialLoad: React.RefObject<boolean>;
  submitOnLoadPerformed: React.RefObject<boolean>;

  // Actions
  refreshChatSessions: () => void;
  onSubmit: (params: {
    message: string;
    currentMessageFiles: ProjectFile[];
    deepResearch: boolean;
    isSeededChat?: boolean;
  }) => Promise<void>;
}

export default function useChatSessionController({
  existingChatSessionId,
  searchParams,
  filterManager,
  firstMessage,
  setSelectedAgentFromId,
  setSelectedDocuments,
  setCurrentMessageFiles,
  chatSessionIdRef,
  loadedIdSessionRef,
  chatInputBarRef,
  isInitialLoad,
  submitOnLoadPerformed,
  refreshChatSessions,
  onSubmit,
}: UseChatSessionControllerProps) {
  const [currentSessionFileTokenCount, setCurrentSessionFileTokenCount] =
    useState<number>(0);
  const [projectFiles, setProjectFiles] = useState<ProjectFile[]>([]);
  // Store actions
  const updateSessionAndMessageTree = useChatSessionStore(
    (state) => state.updateSessionAndMessageTree
  );
  const updateSessionMessageTree = useChatSessionStore(
    (state) => state.updateSessionMessageTree
  );
  const setIsFetchingChatMessages = useChatSessionStore(
    (state) => state.setIsFetchingChatMessages
  );
  const setCurrentSession = useChatSessionStore(
    (state) => state.setCurrentSession
  );
  const initializeSession = useChatSessionStore(
    (state) => state.initializeSession
  );
  const updateCurrentChatSessionSharedStatus = useChatSessionStore(
    (state) => state.updateCurrentChatSessionSharedStatus
  );
  const updateCurrentSelectedNodeForDocDisplay = useChatSessionStore(
    (state) => state.updateCurrentSelectedNodeForDocDisplay
  );
  const currentChatState = useChatSessionStore(
    (state) =>
      state.sessions.get(state.currentSessionId || "")?.chatState || "input"
  );
  const currentChatHistory = useCurrentMessageHistory();
  const chatSessions = useChatSessionStore((state) => state.sessions);
  const { setForcedToolIds } = useForcedTools();

  // Fetch chat messages for the chat session
  useEffect(() => {
    let cancelled = false;
    const priorChatSessionId = chatSessionIdRef.current;
    const loadedSessionId = loadedIdSessionRef.current;
    chatSessionIdRef.current = existingChatSessionId;
    loadedIdSessionRef.current = existingChatSessionId;

    chatInputBarRef.current?.focus();

    const isCreatingNewSession =
      priorChatSessionId === null && existingChatSessionId !== null;
    const isSwitchingBetweenSessions =
      priorChatSessionId !== null &&
      existingChatSessionId !== priorChatSessionId;

    // Clear uploaded files on any session change (they're already in context)
    if (isCreatingNewSession || isSwitchingBetweenSessions) {
      setCurrentMessageFiles([]);
    }

    // Only reset filters/selections when switching between existing sessions
    if (isSwitchingBetweenSessions) {
      setSelectedDocuments([]);
      filterManager.setSelectedDocumentSets([]);
      filterManager.setSelectedTags([]);
      filterManager.setTimeRange(null);

      // Remove uploaded files
      setCurrentMessageFiles([]);

      // If switching from one chat to another, then need to scroll again
      // If we're creating a brand new chat, then don't need to scroll
      if (priorChatSessionId !== null) {
        setSelectedDocuments([]);

        // Clear forced tool ids if and only if we're switching to a new chat session
        setForcedToolIds([]);
      }
    }

    async function initialSessionFetch() {
      if (existingChatSessionId === null) {
        // Clear the current session in the store to show intro messages
        setCurrentSession(null);

        // Reset the selected agent back to default
        setSelectedAgentFromId(null);
        updateCurrentChatSessionSharedStatus(ChatSessionSharedStatus.Private);

        // If we're supposed to submit on initial load, then do that here
        if (
          shouldSubmitOnLoad(searchParams) &&
          !submitOnLoadPerformed.current
        ) {
          submitOnLoadPerformed.current = true;
          await onSubmit({
            message: firstMessage || "",
            currentMessageFiles: [],
            deepResearch: false,
          });
        }
        return;
      }

      // Set the current session first, then set fetching state to prevent intro flash
      setCurrentSession(existingChatSessionId);
      setIsFetchingChatMessages(existingChatSessionId, true);

      const response = await fetch(
        `/api/chat/get-chat-session/${existingChatSessionId}`
      );

      const session = await response.json();
      const chatSession = session as BackendChatSession;
      setSelectedAgentFromId(chatSession.persona_id);

      // Ensure the current session is set to the actual session ID from the response
      setCurrentSession(chatSession.chat_session_id);

      // Initialize session data including personaId
      initializeSession(chatSession.chat_session_id, chatSession);

      const newMessageMap = processRawChatHistory(
        chatSession.messages,
        chatSession.packets
      );
      const newMessageHistory = getLatestMessageChain(newMessageMap);

      // Update message history except for edge where where
      // last message is an error and we're on a new chat.
      // This corresponds to a "renaming" of chat, which occurs after first message
      // stream
      // Read live state from the store (not stale closure) to prevent overwriting
      // an in-flight streaming response with a stale DB snapshot.
      const liveChatState =
        useChatSessionStore.getState().sessions.get(chatSession.chat_session_id)
          ?.chatState || "input";
      console.log("[ctrl] initialSessionFetch done, session=", chatSession.chat_session_id, "liveChatState=", liveChatState, "dbMsgCount=", newMessageHistory.length, "cancelled=", cancelled);
      if (
        !cancelled &&
        (newMessageHistory[newMessageHistory.length - 1]?.type !== "error" ||
          loadedSessionId != null) &&
        !(
          liveChatState == "toolBuilding" ||
          liveChatState == "streaming" ||
          liveChatState == "loading"
        )
      ) {
        console.log("[ctrl] OVERWRITING store with DB data, dbMsgCount=", newMessageHistory.length);
        updateCurrentSelectedNodeForDocDisplay(
          newMessageHistory[newMessageHistory.length - 1]?.nodeId ?? null
        );

        updateSessionAndMessageTree(chatSession.chat_session_id, newMessageMap);
        chatSessionIdRef.current = chatSession.chat_session_id;
      } else {
        console.log("[ctrl] skipping DB overwrite, guarded by chatState, cancelled, or error check");
      }

      setIsFetchingChatMessages(chatSession.chat_session_id, false);

      // Fetch token count for this chat session's project (if any)
      try {
        if (chatSession.chat_session_id) {
          const total = await getSessionProjectTokenCount(
            chatSession.chat_session_id
          );
          setCurrentSessionFileTokenCount(total || 0);
        } else {
          setCurrentSessionFileTokenCount(0);
        }
      } catch (e) {
        setCurrentSessionFileTokenCount(0);
      }

      // Fetch project files for this chat session (if any)
      try {
        if (chatSession.chat_session_id) {
          const files = await getProjectFilesForSession(
            chatSession.chat_session_id
          );
          setProjectFiles(files || []);
        } else {
          setProjectFiles([]);
        }
      } catch (e) {
        setProjectFiles([]);
      }

      // If this is a seeded chat, then kick off the AI message generation
      if (
        newMessageHistory.length === 1 &&
        !submitOnLoadPerformed.current &&
        searchParams?.get(SEARCH_PARAM_NAMES.SEEDED) === "true"
      ) {
        submitOnLoadPerformed.current = true;

        const seededMessage = newMessageHistory[0]?.message;
        if (!seededMessage) {
          return;
        }

        await onSubmit({
          message: seededMessage,
          isSeededChat: true,
          currentMessageFiles: [],
          deepResearch: false,
        });
        // Title is set automatically by backend on first message — just refresh
        if (!chatSession.description) {
          refreshChatSessions();
        }
      } else if (newMessageHistory.length >= 2 && !chatSession.description) {
        refreshChatSessions();
      }
    }

    // SKIP_RELOAD is used after completing the first message in a new session.
    // We don't need to re-fetch at that point, we have everything we need.
    // For safety, we should always re-fetch if there are no messages in the chat history.
    if (
      !searchParams?.get(SEARCH_PARAM_NAMES.SKIP_RELOAD) ||
      currentChatHistory.length === 0
    ) {
      const existingChatSession = existingChatSessionId
        ? chatSessions.get(existingChatSessionId)
        : null;

      // Use live store state to decide — not the render-time snapshot — so
      // an in-flight streaming response is never overwritten by a DB fetch.
      const liveSessionState = useChatSessionStore
        .getState()
        .sessions.get(existingChatSessionId!);
      const liveState = liveSessionState?.chatState || "input";
      console.log("[ctrl] effect fired, sessionId=", existingChatSessionId, "liveState=", liveState, "skipReload=", searchParams?.get(SEARCH_PARAM_NAMES.SKIP_RELOAD), "historyLen=", currentChatHistory.length);
      if (liveState === "input" || liveState === "uploading") {
        initialSessionFetch();
      } else {
        // Session is streaming/loading — skip fetch to avoid clobbering live state.
        console.log("[ctrl] skipping fetch, liveState=", liveState, "calling setCurrentSession only");
        setCurrentSession(existingChatSessionId);
      }
    } else {
      // Remove SKIP_RELOAD param without triggering a page reload
      const currentSearchParams = new URLSearchParams(searchParams?.toString());
      if (currentSearchParams.has(SEARCH_PARAM_NAMES.SKIP_RELOAD)) {
        currentSearchParams.delete(SEARCH_PARAM_NAMES.SKIP_RELOAD);
        const newUrl = `${window.location.pathname}${
          currentSearchParams.toString()
            ? "?" + currentSearchParams.toString()
            : ""
        }`;
        window.history.replaceState({}, "", newUrl);
      }
    }
    return () => {
      cancelled = true;
    };
  }, [
    existingChatSessionId,
    searchParams?.get(SEARCH_PARAM_NAMES.PERSONA_ID),
    // Note: We're intentionally not including all dependencies to avoid infinite loops
    // This effect should only run when existingChatSessionId or persona ID changes
  ]);

  const onMessageSelection = useCallback(
    (nodeId: number) => {
      updateCurrentSelectedNodeForDocDisplay(nodeId);
      const currentMessageTree = useChatSessionStore
        .getState()
        .sessions.get(useChatSessionStore.getState().currentSessionId || "")
        ?.messageTree;

      if (currentMessageTree) {
        const newMessageTree = setMessageAsLatest(currentMessageTree, nodeId);
        const currentSessionId =
          useChatSessionStore.getState().currentSessionId;
        if (currentSessionId) {
          updateSessionMessageTree(currentSessionId, newMessageTree);
        }

        const message = currentMessageTree.get(nodeId);

        if (message?.messageId) {
          // Makes actual API call to set message as latest in the DB so we can
          // edit this message and so it sticks around on page reload
          patchMessageToBeLatest(message.messageId);
        } else {
          console.error("Message has no messageId", nodeId);
        }
      }
    },
    [updateCurrentSelectedNodeForDocDisplay, updateSessionMessageTree]
  );

  return {
    currentSessionFileTokenCount,
    onMessageSelection,
    projectFiles,
  };
}
