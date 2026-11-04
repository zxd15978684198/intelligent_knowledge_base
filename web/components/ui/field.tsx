"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import { Label } from "./label";

/**
 * Field —— 表单一行（标签 + 控件 + 提示/错误）。
 *
 * 不克隆子元素：把 `id` / `aria-describedby` / `aria-invalid` 通过 context 交给
 * `Input` / `Textarea` / `Select` 自己消费。这样 `<Field id="kb-name" ...><Input /></Field>`
 * 天然具备正确的 a11y 关联，而且控件显式传的同名 prop 永远优先。
 */
export interface FieldControlContextValue {
  id?: string;
  describedBy?: string;
  invalid: boolean;
}

const FieldControlContext = React.createContext<FieldControlContextValue | null>(null);

/** 供 Input/Textarea/Select 内部使用；不自定义控件时不要直接调用。 */
export function useFieldControl(): FieldControlContextValue | null {
  return React.useContext(FieldControlContext);
}

export interface FieldProps extends Omit<React.HTMLAttributes<HTMLDivElement>, "id" | "children"> {
  /** 控件的 id，Field 用它关联 label 与错误提示。 */
  id: string;
  label: React.ReactNode;
  /** 输入格式说明；与 error 不同时展示。 */
  hint?: string;
  /** 校验失败信息，非空即 `aria-invalid`。 */
  error?: string;
  required?: boolean;
  children: React.ReactNode;
}

export function Field({
  id,
  label,
  hint,
  error,
  required = false,
  children,
  className,
  ...props
}: FieldProps) {
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy =
    [hintId, errorId].filter((value): value is string => Boolean(value)).join(" ") || undefined;
  const invalid = Boolean(error);

  const contextValue = React.useMemo<FieldControlContextValue>(
    () => ({ id, describedBy, invalid }),
    [id, describedBy, invalid],
  );

  return (
    <div className={cn("flex flex-col gap-1.5", className)} {...props}>
      <Label htmlFor={id} required={required}>
        {label}
      </Label>
      <FieldControlContext.Provider value={contextValue}>{children}</FieldControlContext.Provider>
      {hint ? (
        <p id={hintId} className="text-xs text-fg-muted">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} role="alert" className="text-xs font-medium text-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}
