"use client";

import useSWR from "swr";
import { WellKnownLLMProviderDescriptor } from "@/interfaces/llm";
import { errorHandlingFetcher } from "@/lib/fetcher";
import { useUser } from "@/providers/UserProvider";

export function useLLMProviderOptions() {
  const { isAdmin } = useUser();

  const fetchBuiltInOptions = async (
    url: string
  ): Promise<WellKnownLLMProviderDescriptor[] | undefined> => {
    try {
      return await errorHandlingFetcher<WellKnownLLMProviderDescriptor[]>(url);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      // Treat permission errors as empty catalog so non-admin users can proceed.
      if (message.includes("403")) {
        return [];
      }
      throw error;
    }
  };

  const { data, error, mutate } = useSWR<
    WellKnownLLMProviderDescriptor[] | undefined
  >(isAdmin ? "/api/admin/llm/built-in/options" : null, fetchBuiltInOptions, {
    revalidateOnFocus: false,
    dedupingInterval: 60000, // Dedupe requests within 1 minute
  });

  return {
    llmProviderOptions: isAdmin ? data : [],
    isLoading: isAdmin ? !error && !data : false,
    error: isAdmin ? error : null,
    refetch: mutate,
  };
}
