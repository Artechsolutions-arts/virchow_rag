"use client";

import useSWR from "swr";
import { errorHandlingFetcher } from "@/lib/fetcher";
import {
  LLMProviderDescriptor,
  LLMProviderResponse,
  LLMProviderView,
  WellKnownLLMProviderDescriptor,
} from "@/interfaces/llm";
import { LLM_PROVIDERS_ADMIN_URL } from "@/lib/llmConfig/constants";
import { useUser } from "@/providers/UserProvider";

async function fetchWellKnownProviderList(
  url: string
): Promise<WellKnownLLMProviderDescriptor[]> {
  try {
    return await errorHandlingFetcher<WellKnownLLMProviderDescriptor[]>(url);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    // Non-admin users can receive 403 for built-in catalog endpoints.
    // 404 means the endpoint is disabled (headless mode).
    if (message.includes("403") || message.includes("404")) {
      return [];
    }
    throw error;
  }
}

async function fetchWellKnownProvider(
  url: string
): Promise<WellKnownLLMProviderDescriptor> {
  try {
    return await errorHandlingFetcher<WellKnownLLMProviderDescriptor>(url);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (message.includes("403")) {
      return {
        name: "",
        known_models: [],
        recommended_default_model: null,
      };
    }
    throw error;
  }
}

/**
 * Fetches configured LLM providers accessible to the current user.
 *
 * Hits the **non-admin** endpoints which return `LLMProviderDescriptor`
 * (no `id` or sensitive fields like `api_key`). Use this hook in
 * user-facing UI (chat, popovers, onboarding) where you need the list
 * of providers and their visible models but don't need admin-level details.
 *
 * The backend wraps the provider list in an `LLMProviderResponse` envelope
 * that also carries the global default text and vision models. This hook
 * unwraps `.providers` for convenience while still exposing the defaults.
 *
 * **Endpoints:**
 * - No `personaId` → `GET /api/llm/provider`
 *   Returns all public providers plus restricted providers the user can
 *   access via group membership.
 * - With `personaId` → `GET /api/llm/persona/{personaId}/providers`
 *   Returns providers scoped to a specific persona, respecting RBAC
 *   restrictions. Use this when displaying model options for a particular
 *   assistant.
 *
 * @param personaId - Optional persona ID for RBAC-scoped providers.
 *
 * @returns
 * - `llmProviders` — The array of provider descriptors, or `undefined`
 *    while loading.
 * - `defaultText` — The global (or persona-overridden) default text model.
 * - `defaultVision` — The global (or persona-overridden) default vision model.
 * - `isLoading` — `true` until the first successful response or error.
 * - `error` — The SWR error object, if any.
 * - `refetch` — SWR `mutate` function to trigger a revalidation.
 */
export function useLLMProviders(personaId?: number) {
  const url =
    personaId !== undefined
      ? `/api/llm/persona/${personaId}/providers`
      : "/api/llm/provider";

  const { data, error, mutate } = useSWR<
    LLMProviderResponse<LLMProviderDescriptor>
  >(url, async (u: string) => {
    try {
      return await errorHandlingFetcher<LLMProviderResponse<LLMProviderDescriptor>>(u);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      // 404 means the LLM provider endpoint is disabled (headless mode)
      if (message.includes("404")) {
        return { providers: [], default_text: null, default_vision: null } as LLMProviderResponse<LLMProviderDescriptor>;
      }
      throw err;
    }
  }, {
    revalidateOnFocus: false,
    dedupingInterval: 60000,
  });

  const providers = Array.isArray(data?.providers) ? data.providers : [];

  return {
    llmProviders: providers,
    defaultText: data?.default_text ?? null,
    defaultVision: data?.default_vision ?? null,
    isLoading: !error && !data,
    error,
    refetch: mutate,
  };
}

/**
 * Fetches configured LLM providers via the **admin** endpoint.
 *
 * Hits `GET /api/admin/llm/provider` which returns `LLMProviderView` —
 * the full provider object including `id`, `api_key` (masked),
 * group/persona assignments, and all other admin-visible fields.
 *
 * Use this hook on admin pages (e.g. the LLM Configuration page) where
 * you need provider IDs for mutations (setting defaults, editing, deleting)
 * or need to display admin-only metadata. **Do not use in user-facing UI**
 * — use `useLLMProviders` instead.
 *
 * @returns
 * - `llmProviders` — The array of full provider views, or `undefined`
 *    while loading.
 * - `defaultText` — The global default text model.
 * - `defaultVision` — The global default vision model.
 * - `isLoading` — `true` until the first successful response or error.
 * - `error` — The SWR error object, if any.
 * - `refetch` — SWR `mutate` function to trigger a revalidation.
 */
export function useAdminLLMProviders() {
  const { data, error, mutate } = useSWR<LLMProviderResponse<LLMProviderView>>(
    LLM_PROVIDERS_ADMIN_URL,
    errorHandlingFetcher,
    {
      revalidateOnFocus: false,
      dedupingInterval: 60000,
    }
  );

  const providers = Array.isArray(data?.providers) ? data.providers : [];

  return {
    llmProviders: providers,
    defaultText: data?.default_text ?? null,
    defaultVision: data?.default_vision ?? null,
    isLoading: !error && !data,
    error,
    refetch: mutate,
  };
}

/**
 * Fetches the catalog of well-known (built-in) LLM providers.
 *
 * Hits `GET /api/admin/llm/built-in/options` which returns the static
 * list of provider descriptors that Virchow ships with out of the box
 * (OpenAI, Anthropic, Vertex AI, Bedrock, Azure, Ollama, OpenRouter,
 * etc.). Each descriptor includes the provider's known models and the
 * recommended default model.
 *
 * Used primarily on the LLM Configuration page and onboarding flows
 * to show which providers are available to set up, and to pre-populate
 * model lists before the user has entered credentials.
 *
 * @returns
 * - `wellKnownLLMProviders` — The array of built-in provider descriptors,
 *    or `null` while loading.
 * - `isLoading` — `true` until the first successful response or error.
 * - `error` — The SWR error object, if any.
 * - `mutate` — SWR `mutate` function to trigger a revalidation.
 */
/**
 * Fetches the descriptor for a single well-known (built-in) LLM provider.
 *
 * Hits `GET /api/admin/llm/built-in/options/{providerEndpoint}` which returns
 * the provider descriptor including its known models and the recommended
 * default model.
 *
 * Used inside individual provider modals to pre-populate model lists
 * before the user has entered credentials.
 *
 * @param providerEndpoint - The provider's API endpoint name (e.g. "openai", "anthropic").
 *   Pass `null` to suppress the request.
 */
export function useWellKnownLLMProvider(providerEndpoint: string | null) {
  const { isAdmin } = useUser();
  const { data, error, isLoading } = useSWR<WellKnownLLMProviderDescriptor>(
    isAdmin && providerEndpoint
      ? `/api/admin/llm/built-in/options/${providerEndpoint}`
      : null,
    fetchWellKnownProvider,
    {
      revalidateOnFocus: false,
      dedupingInterval: 60000,
    }
  );

  return {
    wellKnownLLMProvider: isAdmin ? data ?? null : null,
    isLoading: isAdmin ? isLoading : false,
    error: isAdmin ? error : null,
  };
}

export function useWellKnownLLMProviders() {
  const { isAdmin } = useUser();
  const {
    data: wellKnownLLMProviders,
    error,
    isLoading,
    mutate,
  } = useSWR<WellKnownLLMProviderDescriptor[]>(
    isAdmin ? "/api/admin/llm/built-in/options" : null,
    fetchWellKnownProviderList,
    {
      revalidateOnFocus: false,
      dedupingInterval: 60000,
    }
  );

  return {
    wellKnownLLMProviders: isAdmin ? wellKnownLLMProviders ?? null : [],
    isLoading: isAdmin ? isLoading : false,
    error: isAdmin ? error : null,
    mutate,
  };
}
