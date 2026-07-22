export type PetFacing = "left" | "right";

export const DEFAULT_PET_FACING: PetFacing = "right";

export function normalizePetFacing(value: unknown): PetFacing {
  return value === "left" ? "left" : DEFAULT_PET_FACING;
}
