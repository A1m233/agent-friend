/**
 * components/ui 统一出口（barrel）：页面/业务一律从 `@/components/ui` 导入封装件，
 * 不直接 import 子目录。新增通用组件时在此补一行 re-export。
 */
export * from "./badge";
export * from "./button";
export * from "./carousel";
export * from "./collapsible";
export * from "./dialog";
export * from "./status-dot";
export * from "./input";
export * from "./scroll-area";
export * from "./select";
export * from "./separator";
export * from "./sheet";
export * from "./skeleton";
export * from "./tabs";
export * from "./text-ellipsis";
export * from "./tooltip";
export * from "./tooltip-button";
export * from "./sidebar";
export * from "./sonner";
