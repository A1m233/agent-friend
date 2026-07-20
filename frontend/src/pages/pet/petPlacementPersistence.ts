export type PetInitialPlacementPersistenceEvent =
  | { type: "resolved"; hasPoint: boolean; userMoved: boolean }
  | { type: "failed" }
  | { type: "user-owned" };

export function nextPetInitialPlacementSettled(
  current: boolean,
  event: PetInitialPlacementPersistenceEvent,
): boolean {
  if (current || event.type === "user-owned") return true;
  if (event.type === "failed") return false;
  return event.hasPoint || event.userMoved;
}

export function shouldSchedulePetAnchorPersistence(input: {
  isTauri: boolean;
  initialPlacementSettled: boolean;
  isDragging: boolean;
  hasPlacementIntent: boolean;
}): boolean {
  return (
    input.isTauri &&
    input.initialPlacementSettled &&
    !input.isDragging &&
    !input.hasPlacementIntent
  );
}
