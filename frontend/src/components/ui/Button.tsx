import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/utils/cn";

/**
 * 通用按钮（shadcn/ui 风格：cva + cn + forwardRef），颜色走项目语义 token（见 styles/theme）。
 * 这是 components/ui/ 通用原子层的一员；页面/业务禁止散写原生 <button>（ESLint 门禁强制）。
 */
const buttonVariants = cva(
  "inline-flex items-center justify-center whitespace-nowrap font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        solid: "bg-accent text-accent-fg hover:opacity-90",
        outline: "border border-border bg-surface text-fg hover:bg-accent hover:text-accent-fg",
        ghost: "text-fg hover:bg-surface",
      },
      size: {
        sm: "h-8 rounded-md px-3 text-sm",
        md: "h-9 rounded-md px-4 text-sm",
        pill: "h-8 rounded-full px-3 text-sm",
      },
    },
    defaultVariants: { variant: "solid", size: "md" },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, type = "button", ...props }, ref) => (
    <button
      ref={ref}
      type={type}
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    />
  ),
);
Button.displayName = "Button";

export { buttonVariants };
