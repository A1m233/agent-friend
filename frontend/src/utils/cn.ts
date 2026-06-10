import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** 合并 className，解决 Tailwind 类冲突（shadcn/ui 约定）。 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
