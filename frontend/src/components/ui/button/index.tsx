import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { Slot } from "radix-ui";

import { cn } from "@/utils/cn";
import type { IconWeight } from "../icon-weight";
import { Spinner } from "../spinner";

/**
 * shadcn/ui Button（`pnpm dlx shadcn@latest add button` 拉取的官方源码，已按项目约定适配）。
 * 适配点：① 颜色 token 改用项目语义 token（accent / danger / surface / fg…，见 styles/theme）；
 * ② 去掉项目换肤模型用不到的 dark: 变体与 ring/input token；③ 补回项目用到的 pill size；
 * ④ 按项目图标 token 为按钮内 Lucide 声明 regular 粗度，调用方可用 iconWeight 覆盖。
 * asChild 走 radix-ui 的 Slot。页面/业务禁止散写原生 <button>（ESLint 门禁强制）。
 */
const buttonVariants = cva(
  "relative inline-flex shrink-0 select-none items-center justify-center gap-2 rounded-md text-sm font-medium whitespace-nowrap transition-all outline-none focus-visible:ring-[3px] focus-visible:ring-accent/50 aria-invalid:border-danger aria-invalid:ring-danger/20 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        default: "bg-accent text-accent-fg hover:bg-accent/90",
        destructive:
          "bg-danger/10 text-danger hover:bg-danger/20 focus-visible:ring-danger/20",
        outline: "border border-border bg-bg shadow-xs hover:bg-surface-hover hover:text-fg",
        secondary: "bg-surface text-fg hover:bg-surface-hover",
        ghost: "hover:bg-surface hover:text-fg",
        link: "text-accent underline-offset-4 hover:underline",
      },
      size: {
        default: "h-9 px-4 py-2 has-[>svg]:px-3",
        xs: "h-6 gap-1 rounded-md px-2 text-xs has-[>svg]:px-1.5 [&_svg:not([class*='size-'])]:size-3",
        sm: "h-8 gap-1.5 rounded-md px-3 has-[>svg]:px-2.5",
        lg: "h-10 rounded-md px-6 has-[>svg]:px-4",
        icon: "size-9",
        "icon-sm": "size-8",
        "icon-lg": "size-10",
        "icon-xs": "size-6",
        pill: "h-8 rounded-full px-3",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

function Button({
  className,
  variant = "default",
  size = "default",
  iconWeight = "regular",
  asChild = false,
  loading = false,
  disabled,
  children,
  ...props
}: React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean;
    iconWeight?: IconWeight;
    loading?: boolean;
  }) {
  const Comp = asChild ? Slot.Root : "button";

  return (
    <Comp
      data-slot="button"
      data-variant={variant}
      data-size={size}
      data-icon-weight={iconWeight}
      aria-busy={loading || undefined}
      disabled={disabled || loading}
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    >
      {!asChild && loading ? (
        <>
          {React.Children.map(children, (child) =>
            React.isValidElement(child) ? (
              <Slot.Root style={{ visibility: "hidden" }} aria-hidden>
                {child}
              </Slot.Root>
            ) : (
              <span style={{ visibility: "hidden" }} aria-hidden>{child}</span>
            ),
          )}
          <span className="sr-only">{children}</span>
          <span className="absolute inset-0 flex items-center justify-center">
            <Spinner data-icon="inline-start" />
          </span>
        </>
      ) : children}
    </Comp>
  );
}

export { Button, buttonVariants };
