import { forwardRef, type HTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/utils/cn";

/**
 * 状态圆点（shadcn/ui 风格：cva + cn + forwardRef），颜色走项目语义 token（见 styles/theme）。
 * 收敛连接态 / 工具调用态 / loading 等散落的「彩色小圆点」裸 markup，统一一处维护。
 */
const dotVariants = cva("inline-block shrink-0 rounded-full", {
  variants: {
    tone: {
      accent: "bg-accent",
      success: "bg-success",
      danger: "bg-danger",
      warning: "bg-warning",
      muted: "bg-muted",
    },
    size: {
      sm: "size-1.5",
      md: "h-2 w-2",
    },
    pulse: {
      true: "animate-pulse",
      false: "",
    },
  },
  defaultVariants: { tone: "muted", size: "sm", pulse: false },
});

export interface StatusDotProps
  extends Omit<HTMLAttributes<HTMLSpanElement>, "color">,
    VariantProps<typeof dotVariants> {}

export const StatusDot = forwardRef<HTMLSpanElement, StatusDotProps>(
  ({ className, tone, size, pulse, ...props }, ref) => (
    <span ref={ref} className={cn(dotVariants({ tone, size, pulse }), className)} {...props} />
  ),
);
StatusDot.displayName = "StatusDot";

export { dotVariants };
