import useSWR from "swr";
import { Project } from "@/app/app/projects/projectsService";
import { errorHandlingFetcher } from "@/lib/fetcher";

export function useProjects() {
  // Projects feature is disabled in headless mode — return empty data immediately
  return {
    projects: [] as Project[],
    isLoading: false,
    error: null,
    refreshProjects: async () => {},
  };
}
