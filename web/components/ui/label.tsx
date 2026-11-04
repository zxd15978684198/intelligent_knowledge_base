import * as React from "react";

import { cn } from "@/lib/utils";

export interface LabelProps extends React.LabelHTMLAttributes<HTMLLabelElement> {
  /** 必填标记：视觉上补 `*`，同时给读屏补「（必填）」。 */
  required?: boolean;
}

export const Label = React.forwardRef<HTMLLabelElement, LabelProps>(function Label(
  { className, required = false, children, ...props },
  ref,
) {
  return (
    <label
      ref={ref}
      className={cn(
        "inline-flex items-center gap-1 text-sm font-medium text-fg",
        "has-[:disabled]:cursor-not-allowed has-[:disabled]:text-fg-muted",
        className,
      )}
      {...props}
    >
      {children}
      {required ? (
        <>
          <span aria-hidden="true" className="text-danger">
            *
          </span>
          <span className="sr-only">（必填）</span>
        </>
      ) : null}
    </label>
  );
});

Label.displayName = "Label";
