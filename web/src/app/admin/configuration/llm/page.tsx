"use client";

import { useCallback, useEffect, useState } from "react";

interface OllamaModel {
  name: string;
  size?: number;
  modified_at?: string;
}

interface ModelsResponse {
  llm_model: string;
  llm_model_env_default: string;
  embedding_model: string;
  embedding_dim: number;
  ollama_url: string;
  available_models: OllamaModel[];
  ollama_error: string | null;
}

function formatSize(bytes?: number): string {
  if (!bytes) return "";
  const gb = bytes / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  const mb = bytes / 1024 ** 2;
  return `${mb.toFixed(0)} MB`;
}

export default function LanguageModelsPage() {
  const [data, setData] = useState<ModelsResponse | null>(null);
  const [selected, setSelected] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/admin/models", { credentials: "include" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = (await res.json()) as ModelsResponse;
      setData(body);
      setSelected(body.llm_model);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const onSave = async () => {
    if (!selected || !data || selected === data.llm_model) return;
    setSaving(true);
    setError(null);
    setInfo(null);
    try {
      const res = await fetch("/api/admin/models/llm", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: selected }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
      setInfo(
        `Switched LLM model to ${selected}. New chat queries will use it.`
      );
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="px-6 py-6 max-w-4xl mx-auto">
      <h1 className="text-xl font-semibold mb-1">Language Models</h1>
      <p className="text-sm text-text-500 mb-6">
        Models that power answer generation and embeddings. The system reads
        them from Ollama at{" "}
        <code className="text-xs px-1 py-0.5 rounded bg-background-tint-02">
          {data?.ollama_url || "…"}
        </code>
        .
      </p>

      {error && (
        <div className="mb-4 rounded-md border border-red-300 bg-red-50 text-red-700 text-sm p-3">
          {error}
        </div>
      )}
      {info && (
        <div className="mb-4 rounded-md border border-emerald-300 bg-emerald-50 text-emerald-700 text-sm p-3">
          {info}
        </div>
      )}
      {data?.ollama_error && (
        <div className="mb-4 rounded-md border border-amber-300 bg-amber-50 text-amber-700 text-sm p-3">
          Could not reach Ollama: {data.ollama_error}. The picker below will be
          empty until Ollama is reachable.
        </div>
      )}

      {/* ── Current models ─────────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
        <div className="rounded-lg border border-border-200 bg-background-tint-00 p-4">
          <div className="text-xs uppercase text-text-400 mb-1">Answer LLM</div>
          <div className="font-mono text-sm break-all">
            {data?.llm_model || "…"}
          </div>
          {data && data.llm_model !== data.llm_model_env_default && (
            <div className="mt-2 text-xs text-text-500">
              Runtime override of the env default (
              <span className="font-mono">{data.llm_model_env_default}</span>).
            </div>
          )}
        </div>
        <div className="rounded-lg border border-border-200 bg-background-tint-00 p-4">
          <div className="text-xs uppercase text-text-400 mb-1">
            Embedding model
          </div>
          <div className="font-mono text-sm break-all">
            {data?.embedding_model || "…"}
          </div>
          <div className="mt-2 text-xs text-text-500">
            Dimension: {data?.embedding_dim || "…"}. Read-only — changing
            requires re-indexing every document.
          </div>
        </div>
      </div>

      {/* ── Switch LLM model ───────────────────────────────────────────────── */}
      <div className="rounded-lg border border-border-200 bg-background-tint-00 p-4">
        <div className="text-sm font-semibold mb-2">Change the answer LLM</div>
        <p className="text-xs text-text-500 mb-3">
          New chat queries start using the selected model immediately (within
          ~15 seconds, the cache TTL). The embedding model stays the same.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col min-w-[260px] flex-1">
            <label className="text-xs text-text-500 mb-1">
              Installed Ollama models
            </label>
            <select
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
              className="border border-border-300 rounded-md px-2 py-2 text-sm bg-white"
              disabled={!data || saving}
            >
              {(data?.available_models || []).length === 0 && (
                <option value={data?.llm_model || ""}>
                  {data?.llm_model || "—"}
                </option>
              )}
              {(data?.available_models || []).map((m) => (
                <option key={m.name} value={m.name}>
                  {m.name}
                  {m.size ? ` — ${formatSize(m.size)}` : ""}
                </option>
              ))}
            </select>
          </div>
          <button
            type="button"
            onClick={onSave}
            disabled={
              !selected || !data || selected === data.llm_model || saving
            }
            className="px-4 py-2 rounded-md bg-accent text-accent-foreground text-sm font-medium disabled:opacity-40 hover:opacity-90"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
        {data && (data.available_models?.length || 0) === 0 && (
          <div className="mt-3 text-xs text-text-500">
            No models reported by Ollama. Pull one on the host first, e.g.{" "}
            <code className="text-xs px-1 py-0.5 rounded bg-background-tint-02">
              ollama pull qwen2.5:14b-instruct
            </code>
            .
          </div>
        )}
      </div>
    </div>
  );
}
