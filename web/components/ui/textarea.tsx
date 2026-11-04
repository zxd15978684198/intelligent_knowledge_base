"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import { useFieldControl } from "./field";

export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  /** 校验失败态；放在 `<Field>` 里且 `error` 非空时会自动置真。 */
  invalid?: boolean;
}

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { className, invalid = false, id, "aria-describedby": ariaDescribedBy, rows = 4, ...props },
  ref,
) {
  const field = useFieldControl();
  const isInvalid = invalid || Boolean(field?.invalid);

  return (
    <textarea
      ref={ref}
      id={id ?? field?.id}
      rows={rows}
      aria-describedby={ariaDescribedBy ?? field?.describedBy}
      aria-invalid={isInvalid || undefined}
      className={cn(
        "w-full resize-y rounded-md border border-border bg-surface px-3 py-2 text-sm text-fg",
        "min-h-24",
        "transition-colors duration-150 ease-out",
        "placeholder:text-fg-subtle",
        "hover:border-border-strong",
        "focus:border-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary",
        "disabled:cursor-not-allowed disabled:bg-surface-muted disabled:text-fg-muted",
        "read-only:bg-surface-muted",
        "aria-invalid:border-danger aria-invalid:bg-danger-weak",
        className,
      )}
      {...props}
    />
  );
});

Textarea.displayName = "Textarea";
