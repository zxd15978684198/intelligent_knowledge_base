import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

/**
 * Button —— 语义令牌 + cva 变体（计划 §6：默认/悬停/焦点/按下/禁用/加载 全状态）。
 *
 * 触控目标：`sm` 与 `md` 都是 `min-h-hit`（44px，令牌 `--spacing-hit`），
 * `sm` 只收窄左右内边距，不降低高度——否则移动端达不到 44px。
 * 焦点环由 `globals.css` 的 `:focus-visible` 统一给（不允许 outline:none）。
 */
const buttonVariants = cva(
  [
    "inline-flex items-center justify-center gap-2",
    "min-h-hit",
    "rounded-md border",
    "text-sm font-medium whitespace-nowrap",
    "transition-[transform,box-shadow,background-color,border-color,color,opacity] duration-fast ease-out",
    "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-ring",
    "active:scale-[0.98]",
    "disabled:pointer-events-none disabled:opacity-60",
    "aria-disabled:pointer-events-none aria-disabled:opacity-60",
  ],
  {
    variants: {
      variant: {
        primary:
          "border-primary bg-primary text-primary-fg shadow-sm hover:border-primary-hover hover:bg-primary-hover hover:shadow-md hover:-translate-y-px active:bg-primary-hover active:shadow-sm active:translate-y-0",
        secondary:
          "border-border bg-surface text-fg hover:border-border-strong hover:bg-surface-muted hover:shadow-sm hover:-translate-y-px active:bg-surface-muted active:translate-y-0",
        ghost:
          "border-transparent bg-transparent text-fg hover:bg-surface-muted active:bg-surface-muted",
        danger:
          "border-danger bg-danger text-fg-inverse shadow-sm hover:brightness-95 hover:-translate-y-px active:brightness-90 active:translate-y-0",
      },
      size: {
        sm: "px-3",
        md: "px-4",
      },
    },
    defaultVariants: {
      variant: "primary",
      size: "md",
    },
  },
);

export type ButtonVariant = NonNullable<VariantProps<typeof buttonVariants>["variant"]>;
export type ButtonSize = NonNullable<VariantProps<typeof buttonVariants>["size"]>;

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  /** 把样式合并到子元素上（例如把 `next/link` 渲染成按钮），Radix Slot 语义。 */
  asChild?: boolean;
  /** 加载中：禁用交互 + `aria-busy`，并渲染转圈图标。 */
  loading?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant, size, asChild = false, loading = false, disabled, children, type, onClick, ...props },
  ref,
) {
  const isDisabled = Boolean(disabled) || loading;
  const classes = cn(buttonVariants({ variant, size }), className);

  // 键盘/点击一律走这里：原生 disabled 已拦，asChild 分支（<a> 等）没有 disabled，
  // 只能靠 preventDefault —— 加载中重复提交是这类组件最容易漏的一条。
  //
  // 事件类型是 `MouseEvent<HTMLElement>`（asChild 时 currentTarget 是 <a> 而非 <button>），
  // 而 `onClick` 的签名来自 `ButtonHTMLAttributes`（`MouseEvent<HTMLButtonElement>`）。
  // 两者只在 `currentTarget` 的静态类型上不同，运行时是同一个事件对象，这里收窄一次；
  // 调用方拿到的仍然是自己的事件，不会丢失 preventDefault/stopPropagation。
  const handleClick = React.useCallback<React.MouseEventHandler<HTMLElement>>(
    (event) => {
      if (isDisabled) {
        event.preventDefault();
        return;
      }
      onClick?.(event as React.MouseEvent<HTMLButtonElement>);
    },
    [isDisabled, onClick],
  );

  const shared = {
    className: classes,
    "aria-busy": loading || undefined,
    "aria-disabled": isDisabled || undefined,
    "data-loading": loading || undefined,
  } as const;

  if (asChild) {
    const child = React.Children.only(children) as React.ReactElement<{
      children?: React.ReactNode;
      onClick?: React.MouseEventHandler<HTMLElement>;
    }>;
    const childOnClick = child.props.onClick;

    // 不能把守卫交给 Slot：Radix Slot 合并同名事件处理函数时**先调用子元素自己的**
    // 处理器，再调用 Slot 上的，preventDefault 拦不住已经跑掉的用户回调。
    // 所以这里在 cloneElement 里合成一个守卫：禁用态直接吞掉，否则子元素 → 组件 onClick。
    const guardedClick: React.MouseEventHandler<HTMLElement> = (event) => {
      if (isDisabled) {
        event.preventDefault();
        return;
      }
      childOnClick?.(event);
      onClick?.(event as React.MouseEvent<HTMLButtonElement>);
    };

    // Slot 只接受单一子元素，所以加载图标必须注入子元素内部，不能当兄弟节点。
    const slotted = React.cloneElement(
      child,
      { onClick: guardedClick },
      loading ? (
        <>
          <Spinner />
          {child.props.children}
        </>
      ) : (
        child.props.children
      ),
    );

    return (
      <Slot ref={ref} {...shared} {...props}>
        {slotted}
      </Slot>
    );
  }

  return (
    <button
      ref={ref}
      type={type ?? "button"}
      disabled={isDisabled}
      {...shared}
      {...props}
      onClick={handleClick}
    >
      {loading ? <Spinner /> : null}
      {children}
    </button>
  );
});

Button.displayName = "Button";

function Spinner() {
  return (
    <span
      data-testid="button-spinner"
      aria-hidden="true"
      className="size-4 shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent"
    />
  );
}

export { buttonVariants };
