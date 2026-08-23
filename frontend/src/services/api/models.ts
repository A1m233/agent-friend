import { http } from "../http";
import type { ModelRuntimeConfig } from "@/lib/model-config";

export interface ModelValidationResult {
  model: string;
  available: true;
  runtimeConfig?: ModelRuntimeConfig;
}

export const modelsApi = {
  validate(configOrModel: ModelRuntimeConfig | string): Promise<ModelValidationResult> {
    const config = typeof configOrModel === "string"
      ? { model: configOrModel, reasoningPreset: undefined }
      : configOrModel;
    return http.post<unknown, ModelValidationResult>("/v1/models/validate", {
      model: config.model,
      reasoningPreset: config.reasoningPreset,
    });
  },
};
