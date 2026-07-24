import { http } from "../http";

export interface ModelValidationResult {
  model: string;
  available: true;
}

export const modelsApi = {
  validate(model: string): Promise<ModelValidationResult> {
    return http.post<unknown, ModelValidationResult>("/v1/models/validate", { model });
  },
};
