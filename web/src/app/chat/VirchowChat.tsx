"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useRouter } from "next/navigation";

interface Message {
  role: "user" | "assistant";
  content: string;
  sources?: string[];
}

interface Chat {
  id: string;
  title: string | null;
  created_at: string;
}

export default function VirchowChat() {
  const router = useRouter();
  const [chats, setChats] = useState<Chat[]>([]);
  const [currentChatId, setCurrentChatId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, loading]);

  useEffect(() => {
    loadChats();
  }, []);

  async function loadChats() {
    try {
      const res = await fetch("/api/virchow/chats");
      if (res.ok) {
        const data = await res.json();
        setChats(data);
      }
    } catch {}
  }

  async function openChat(id: string) {
    setCurrentChatId(id);
    try {
      const res = await fetch(`/api/virchow/chats/${id}/messages`);
      if (res.ok) {
        const data = await res.json();
        setMessages(
          data.map((m: { role: string; content: string }) => ({
            role: m.role === "user" ? "user" : "assistant",
            content: m.content,
          }))
        );
      }
    } catch {}
  }

  function newChat() {
    setCurrentChatId(null);
    setMessages([]);
  }

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/auth/login");
  }

  async function sendMessage() {
    const text = input.trim();
    if (!text || loading) return;

    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setInput("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
    setLoading(true);

    try {
      const res = await fetch("/api/virchow/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: text, chat_id: currentChatId }),
      });

      if (res.status === 401) {
        router.push("/auth/login");
        return;
      }

      const data = await res.json();
      setCurrentChatId(data.chat_id || currentChatId);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: data.answer || "No answer returned.",
          sources: data.citations,
        },
      ]);
      loadChats();
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Failed to get a response. Please try again." },
      ]);
    } finally {
      setLoading(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }

  function handleInput(e: React.ChangeEvent<HTMLTextAreaElement>) {
    setInput(e.target.value);
    e.target.style.height = "auto";
    e.target.style.height = Math.min(e.target.scrollHeight, 160) + "px";
  }

  return (
    <div className="flex h-screen w-full bg-background text-text overflow-hidden">
      {/* Sidebar */}
      <aside
        className={`flex flex-col bg-background-sidebar border-r border-border-200 transition-all duration-200 ${
          sidebarOpen ? "w-64" : "w-0 overflow-hidden"
        }`}
      >
        <div className="flex items-center justify-between px-4 py-4 border-b border-border-200">
          <span className="font-semibold text-lg text-text-900">Virchow</span>
          <button
            onClick={() => setSidebarOpen(false)}
            className="text-text-500 hover:text-text-900 p-1 rounded"
          >
            ✕
          </button>
        </div>

        <div className="px-3 py-2">
          <button
            onClick={newChat}
            className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-text-700 hover:bg-hover border border-dashed border-border-300 transition-colors"
          >
            <span className="text-lg leading-none">+</span>
            New Chat
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-3 py-1">
          {chats.length === 0 && (
            <p className="text-xs text-text-400 px-2 py-2">No chats yet</p>
          )}
          {chats.map((chat) => (
            <button
              key={chat.id}
              onClick={() => openChat(chat.id)}
              className={`w-full text-left px-3 py-2 rounded-lg text-sm mb-1 truncate transition-colors ${
                currentChatId === chat.id
                  ? "bg-accent text-accent-foreground"
                  : "text-text-700 hover:bg-hover"
              }`}
            >
              {chat.title || "Untitled"}
            </button>
          ))}
        </div>

        <div className="px-3 py-3 border-t border-border-200">
          <button
            onClick={logout}
            className="w-full text-left px-3 py-2 rounded-lg text-sm text-text-500 hover:text-red-500 hover:bg-hover transition-colors"
          >
            Sign out
          </button>
        </div>
      </aside>

      {/* Main */}
      <div className="flex flex-col flex-1 overflow-hidden">
        {/* Top bar */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-border-200">
          {!sidebarOpen && (
            <button
              onClick={() => setSidebarOpen(true)}
              className="text-text-500 hover:text-text-900 p-1 rounded"
            >
              ☰
            </button>
          )}
          <span className="text-sm font-medium text-text-700">
            {currentChatId
              ? chats.find((c) => c.id === currentChatId)?.title || "Chat"
              : "New Chat"}
          </span>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 py-6">
          {messages.length === 0 && !loading && (
            <div className="flex flex-col items-center justify-center h-full gap-3 text-text-400">
              <div className="text-4xl">🔍</div>
              <p className="text-lg font-medium text-text-700">
                Ask anything about your knowledge base
              </p>
              <p className="text-sm">
                Your questions are answered using document context retrieved from
                the knowledge base.
              </p>
            </div>
          )}

          <div className="max-w-3xl mx-auto flex flex-col gap-4">
            {messages.map((msg, i) => (
              <div
                key={i}
                className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={`max-w-[80%] rounded-2xl px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap ${
                    msg.role === "user"
                      ? "bg-accent text-accent-foreground rounded-br-sm"
                      : "bg-background-125 text-text-900 rounded-bl-sm border border-border-200"
                  }`}
                >
                  {msg.content}
                  {msg.sources && msg.sources.length > 0 && (
                    <div className="mt-2 pt-2 border-t border-border-200 text-xs text-text-400">
                      Sources: {msg.sources.join(", ")}
                    </div>
                  )}
                </div>
              </div>
            ))}

            {loading && (
              <div className="flex justify-start">
                <div className="bg-background-125 border border-border-200 rounded-2xl rounded-bl-sm px-4 py-3">
                  <div className="flex gap-1">
                    {[0, 1, 2].map((i) => (
                      <span
                        key={i}
                        className="w-2 h-2 bg-text-400 rounded-full animate-bounce"
                        style={{ animationDelay: `${i * 0.15}s` }}
                      />
                    ))}
                  </div>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* Input */}
        <div className="px-4 py-4 border-t border-border-200">
          <div className="max-w-3xl mx-auto flex items-end gap-3">
            <div className="flex-1 bg-background-100 border border-border-200 rounded-xl overflow-hidden focus-within:border-accent transition-colors">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={handleInput}
                onKeyDown={handleKeyDown}
                rows={1}
                placeholder="Ask a question…"
                className="w-full px-4 py-3 bg-transparent text-sm text-text-900 placeholder:text-text-400 resize-none outline-none max-h-40"
                disabled={loading}
              />
            </div>
            <button
              onClick={sendMessage}
              disabled={loading || !input.trim()}
              className="flex-shrink-0 w-10 h-10 rounded-xl bg-accent text-accent-foreground flex items-center justify-center disabled:opacity-40 hover:opacity-90 transition-opacity"
            >
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <line x1="22" y1="2" x2="11" y2="13" />
                <polygon points="22 2 15 22 11 13 2 9 22 2" />
              </svg>
            </button>
          </div>
          <p className="text-xs text-text-400 text-center mt-2">
            Answers are grounded in your knowledge base documents.
          </p>
        </div>
      </div>
    </div>
  );
}
