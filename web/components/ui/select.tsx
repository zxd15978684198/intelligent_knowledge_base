"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import { useFieldControl } from "./field";

export interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  /** 校验失败态；放在 `<Field>` 里且 `error` 非空时会自动置真。 */
  invalid?: boolean;
}

/**
 * 原生 `<select>` 包一层：保留系统下拉与键盘行为（尤其是移动端滚轮），
 * 只统一边框、间距与 44px 触控高度。选项内容由调用方通过 children 提供。
 */
export const Select = React.forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, invalid = false, id, "aria-describedby": ariaDescribedBy, children, ...props },
  ref,
) {
  const field = useFieldControl();
  const isInvalid = invalid || Boolean(field?.invalid);

  return (
    <select
      ref={ref}
      id={id ?? field?.id}
      aria-describedby={ariaDescribedBy ?? field?.describedBy}
      aria-invalid={isInvalid || undefined}
      className={cn(
        "min-h-hit w-full rounded-md border border-border bg-surface px-3 py-2 pr-8 text-sm text-fg",
        "transition-colors duration-150 ease-out",
        "hover:border-border-strong",
        "focus:border-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary",
        "disabled:cursor-not-allowed disabled:bg-surface-muted disabled:text-fg-muted",
        "aria-invalid:border-danger aria-invalid:bg-danger-weak",
        className,
      )}
      {...props}
    >
      {children}
    </select>
  );
});

Select.displayName = "Select";
