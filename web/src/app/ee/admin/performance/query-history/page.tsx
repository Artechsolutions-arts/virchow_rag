"use client";

import { useState, useEffect } from "react";

interface UserSummary {
  id: string;
  email: string;
  name: string;
  role: string;
  chat_count: number;
}

interface ChatMessage {
  role: string;
  content: string;
  created_at: string;
}

interface ChatSession {
  chat_id: string;
  title: string;
  created_at: string;
  messages: ChatMessage[];
}

interface UserHistory {
  user: { id: string; email: string; name: string; role: string };
  chats: ChatSession[];
}

export default function UsersHistoryPage() {
  const [users, setUsers] = useState<UserSummary[]>([]);
  const [selected, setSelected] = useState<UserSummary | null>(null);
  const [history, setHistory] = useState<UserHistory | null>(null);
  const [loadingUsers, setLoadingUsers] = useState(true);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [search, setSearch] = useState("");
  const [openChats, setOpenChats] = useState<Set<string>>(new Set());

  useEffect(() => {
    fetch("/api/admin/users-history")
      .then((r) => r.json())
      .then((data) => {
        setUsers(Array.isArray(data) ? data : []);
        setLoadingUsers(false);
      })
      .catch(() => setLoadingUsers(false));
  }, []);

  const selectUser = async (u: UserSummary) => {
    setSelected(u);
    setHistory(null);
    setOpenChats(new Set());
    setLoadingHistory(true);
    try {
      const r = await fetch(`/api/admin/users-history/${u.id}`);
      const data = await r.json();
      setHistory(data);
    } finally {
      setLoadingHistory(false);
    }
  };

  const toggleChat = (chatId: string) => {
    setOpenChats((prev) => {
      const next = new Set(prev);
      next.has(chatId) ? next.delete(chatId) : next.add(chatId);
      return next;
    });
  };

  const filtered = users.filter(
    (u) =>
      u.name.toLowerCase().includes(search.toLowerCase()) ||
      u.email.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="flex h-full min-h-screen">
      {/* Left panel — user list */}
      <div className="w-72 shrink-0 border-r border-neutral-200 dark:border-neutral-700 flex flex-col">
        <div className="p-4 border-b border-neutral-200 dark:border-neutral-700">
          <h2 className="text-base font-semibold mb-3">Users</h2>
          <input
            type="text"
            placeholder="Search users..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full px-3 py-1.5 text-sm border border-neutral-300 dark:border-neutral-600 rounded-lg bg-white dark:bg-neutral-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
        <div className="flex-1 overflow-y-auto">
          {loadingUsers ? (
            <div className="p-4 text-sm text-neutral-500">Loading...</div>
          ) : filtered.length === 0 ? (
            <div className="p-4 text-sm text-neutral-500">No users found.</div>
          ) : (
            filtered.map((u) => (
              <button
                key={u.id}
                onClick={() => selectUser(u)}
                className={`w-full text-left px-4 py-3 border-b border-neutral-100 dark:border-neutral-800 hover:bg-neutral-50 dark:hover:bg-neutral-800 transition-colors ${
                  selected?.id === u.id
                    ? "bg-blue-50 dark:bg-blue-900/30 border-l-2 border-l-blue-500"
                    : ""
                }`}
              >
                <div className="text-sm font-medium truncate">{u.name}</div>
                <div className="text-xs text-neutral-500 truncate">{u.email}</div>
                <div className="flex items-center gap-2 mt-0.5">
                  <span className="text-xs text-neutral-400 capitalize">{u.role}</span>
                  <span className="text-xs text-neutral-400">·</span>
                  <span className="text-xs text-neutral-400">{u.chat_count} chats</span>
                </div>
              </button>
            ))
          )}
        </div>
      </div>

      {/* Right panel — chat history */}
      <div className="flex-1 overflow-y-auto">
        {!selected ? (
          <div className="flex items-center justify-center h-full text-neutral-400 text-sm">
            Select a user to view their chat history.
          </div>
        ) : loadingHistory ? (
          <div className="p-6 text-sm text-neutral-500">Loading history...</div>
        ) : !history ? (
          <div className="p-6 text-sm text-red-500">Failed to load history.</div>
        ) : (
          <div className="max-w-3xl mx-auto p-6">
            <div className="mb-6">
              <h1 className="text-xl font-semibold">{history.user.name}</h1>
              <p className="text-sm text-neutral-500">{history.user.email}</p>
              <span className="inline-block mt-1 px-2 py-0.5 text-xs rounded-full bg-neutral-100 dark:bg-neutral-800 capitalize">
                {history.user.role}
              </span>
            </div>

            {history.chats.length === 0 ? (
              <p className="text-sm text-neutral-400">No chat history for this user.</p>
            ) : (
              <div className="space-y-3">
                <p className="text-xs text-neutral-400 uppercase tracking-wide font-medium">
                  {history.chats.length} conversation{history.chats.length !== 1 ? "s" : ""}
                </p>
                {history.chats.map((chat) => (
                  <div
                    key={chat.chat_id}
                    className="border border-neutral-200 dark:border-neutral-700 rounded-xl overflow-hidden"
                  >
                    <button
                      onClick={() => toggleChat(chat.chat_id)}
                      className="w-full text-left px-4 py-3 flex items-center justify-between hover:bg-neutral-50 dark:hover:bg-neutral-800 transition-colors"
                    >
                      <div>
                        <span className="text-sm font-medium">{chat.title}</span>
                        {chat.created_at && (
                          <span className="ml-2 text-xs text-neutral-400">
                            {new Date(chat.created_at).toLocaleDateString()}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-neutral-400">
                          {chat.messages.length} msg{chat.messages.length !== 1 ? "s" : ""}
                        </span>
                        <span className="text-neutral-400 text-xs">
                          {openChats.has(chat.chat_id) ? "▲" : "▼"}
                        </span>
                      </div>
                    </button>

                    {openChats.has(chat.chat_id) && (
                      <div className="border-t border-neutral-200 dark:border-neutral-700 divide-y divide-neutral-100 dark:divide-neutral-800">
                        {chat.messages.length === 0 ? (
                          <p className="px-4 py-3 text-xs text-neutral-400">No messages.</p>
                        ) : (
                          chat.messages.map((msg, i) => (
                            <div
                              key={i}
                              className={`px-4 py-3 text-sm ${
                                msg.role === "user"
                                  ? "bg-white dark:bg-neutral-900"
                                  : "bg-neutral-50 dark:bg-neutral-800/50"
                              }`}
                            >
                              <div className="flex items-center gap-2 mb-1">
                                <span
                                  className={`text-xs font-semibold uppercase ${
                                    msg.role === "user" ? "text-blue-600" : "text-green-600"
                                  }`}
                                >
                                  {msg.role === "user" ? "User" : "Assistant"}
                                </span>
                                {msg.created_at && (
                                  <span className="text-xs text-neutral-400">
                                    {new Date(msg.created_at).toLocaleString()}
                                  </span>
                                )}
                              </div>
                              <p className="whitespace-pre-wrap text-neutral-700 dark:text-neutral-300">
                                {msg.content}
                              </p>
                            </div>
                          ))
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
