import { JSX } from "react";
import {
  AzureIcon,
  CohereIcon,
  GoogleIcon,
  IconProps,
  LiteLLMIcon,
  MicrosoftIcon,
  NomicIcon,
  OllamaIcon,
  OpenAIISVG,
  OpenSourceIcon,
  VoyageIconSVG,
} from "@/components/icons/icons";
import { SwitchoverType } from "@/app/admin/embeddings/interfaces";
import { DOCS_ADMINS_PATH } from "@/lib/constants";

export enum EmbeddingProvider {
  OLLAMA = "ollama",
}

export interface CloudEmbeddingProvider {
  provider_type: EmbeddingProvider;
  api_key?: string;
  api_url?: string;
  custom_config?: Record<string, string>;
  docsLink?: string;

  // Frontend-specific properties
  website: string;
  icon: ({ size, className }: IconProps) => JSX.Element;
  description: string;
  apiLink: string;
  costslink?: string;

  // Relationships
  embedding_models: CloudEmbeddingModel[];
  default_model?: CloudEmbeddingModel;
}

// Embedding Models
export interface EmbeddingModelDescriptor {
  id?: number;
  model_name: string;
  model_dim: number;
  normalize: boolean;
  query_prefix: string;
  passage_prefix: string;
  provider_type: EmbeddingProvider | null;
  description: string;
  api_key: string | null;
  api_url: string | null;
  api_version?: string | null;
  deployment_name?: string | null;
  index_name: string | null;
  switchover_type?: SwitchoverType;
}

export interface CloudEmbeddingModel extends EmbeddingModelDescriptor {
  pricePerMillion: number;
}

export interface HostedEmbeddingModel extends EmbeddingModelDescriptor {
  link?: string;
  isDefault?: boolean;
}

// Responses
export interface FullEmbeddingModelResponse {
  current_model_name: string;
  secondary_model_name: string | null;
}

export interface CloudEmbeddingProviderFull extends CloudEmbeddingProvider {
  configured?: boolean;
}

export const AVAILABLE_MODELS: HostedEmbeddingModel[] = [
  {
    model_name: "nomic-ai/nomic-embed-text-v1",
    model_dim: 768,
    normalize: true,
    description:
      "The recommended default for most situations. If you aren't sure which model to use, this is probably the one.",
    isDefault: true,
    link: "https://huggingface.co/nomic-ai/nomic-embed-text-v1",
    query_prefix: "search_query: ",
    passage_prefix: "search_document: ",
    index_name: "",
    provider_type: null,
    api_key: null,
    api_url: null,
  },
];

export const LITELLM_CLOUD_PROVIDER: CloudEmbeddingProvider | null = null;
export const AZURE_CLOUD_PROVIDER: CloudEmbeddingProvider | null = null;

export const OLLAMA_CLOUD_PROVIDER: CloudEmbeddingProvider = {
  provider_type: EmbeddingProvider.OLLAMA,
  website: "https://ollama.ai",
  icon: OllamaIcon,
  description: "Get up and running with large language models locally.",
  apiLink: "https://ollama.ai/library",
  embedding_models: [], // No default embedding models
};

export const AVAILABLE_CLOUD_PROVIDERS: CloudEmbeddingProvider[] = [
  OLLAMA_CLOUD_PROVIDER,
];

export const getFormattedProviderName = (providerType: string | null) => {
  if (!providerType) return "Self-hosted";

  switch (providerType) {
    case "openai":
      return "OpenAI";
    case "cohere":
      return "Cohere";
    case "voyage":
      return "Voyage AI";
    case "google":
      return "Google";
    case "litellm":
      return "LiteLLM";
    case "azure":
      return "Azure";
    case "ollama":
      return "Ollama";
    default:
      return providerType.charAt(0).toUpperCase() + providerType.slice(1);
  }
};

export const getTitleForRerankType = (type: string) => {
  switch (type) {
    case "nomic-ai":
      return "Nomic (recommended)";
    case "intfloat":
      return "Microsoft";
    default:
      return "Open Source";
  }
};

export const getIconForRerankType = (type: string) => {
  switch (type) {
    case "nomic-ai":
      return <NomicIcon size={40} />;
    case "intfloat":
      return <MicrosoftIcon size={40} />;
    default:
      return <OpenSourceIcon size={40} />;
  }
};

export const INVALID_OLD_MODEL = "thenlper/gte-small";

export function checkModelNameIsValid(
  modelName: string | undefined | null
): boolean {
  return !!modelName && modelName !== INVALID_OLD_MODEL;
}
