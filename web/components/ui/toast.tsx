"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import type { BadgeTone } from "./badge";
import { CloseIcon } from "./icons";
import styles from "./ui-motion.module.css";

/**
 * Toast —— 极简实现（计划 D5：不引第三方 toast 库）。
 *
 * 设计要点：
 * - 两个**常驻** live region（`role="status"` 礼貌 / `role="alert"` 强打断）。先有区域、
 *   后有内容，读屏才会播报；只把 `role` 放在"插入即满内容"的节点上，多数读屏会漏。
 * - 危险态进 alert 区域并延长停留时间（8s），其余 5s；关闭按钮 44px。
 * - 浮层深度：`shadow-lg`（升级计划 §1.6 P3），入场用 CSS keyframes 的
 *   translateY+opacity（只动 transform/opacity），`prefers-reduced-motion` 由
 *   `globals.css` 的全局降级 + 本文件 `.toast-enter` 的媒体查询双保险关掉。
 */
export type ToastTone = BadgeTone;

export interface ToastOptions {
  title: string;
  description?: string;
  /** 默认 `neutral`；`danger` 会进强打断区域。 */
  tone?: ToastTone;
  /** 自动关闭毫秒数；不传按 tone 取 5000 / 8000。 */
  durationMs?: number;
}

export interface ToastRecord {
  id: string;
  title: string;
  description?: string;
  tone: ToastTone;
}

export interface ToastApi {
  /** 弹出一条提示，返回 id（可用于提前 `dismiss`）。只能在事件处理/hook 回调里调用。 */
  toast: (options: ToastOptions) => string;
  dismiss: (id: string) => void;
}

const ToastApiContext = React.createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const api = React.useContext(ToastApiContext);
  if (!api) {
    throw new Error("useToast 必须在 <ToastProvider> 内部使用");
  }
  return api;
}

export interface ToastProviderProps {
  children: React.ReactNode;
  /** 同时最多保留几条，超出丢弃最旧的。 */
  maxToasts?: number;
}

export function ToastProvider({ children, maxToasts = 4 }: ToastProviderProps) {
  const [toasts, setToasts] = React.useState<ToastRecord[]>([]);
  const timers = React.useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const sequence = React.useRef(0);

  const dismiss = React.useCallback((id: string) => {
    setToasts((current) => current.filter((item) => item.id !== id));
    const timer = timers.current.get(id);
    if (timer !== undefined) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
  }, []);

  const toast = React.useCallback(
    (options: ToastOptions) => {
      sequence.current += 1;
      const id = `toast-${sequence.current}`;
      const tone = options.tone ?? "neutral";
      const record: ToastRecord = {
        id,
        title: options.title,
        description: options.description,
        tone,
      };
      const limit = Math.max(1, maxToasts);
      setToasts((current) => [...current, record].slice(-limit));
      const timeout = options.durationMs ?? (tone === "danger" ? 8000 : 5000);
      timers.current.set(
        id,
        setTimeout(() => dismiss(id), timeout),
      );
      return id;
    },
    [dismiss, maxToasts],
  );

  React.useEffect(() => {
    const pending = timers.current;
    return () => {
      pending.forEach((timer) => clearTimeout(timer));
      pending.clear();
    };
  }, []);

  const api = React.useMemo<ToastApi>(() => ({ toast, dismiss }), [toast, dismiss]);

  const polite = toasts.filter((item) => item.tone !== "danger");
  const assertive = toasts.filter((item) => item.tone === "danger");

  return (
    <ToastApiContext.Provider value={api}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-0 z-50 flex flex-col items-center gap-2 p-4 sm:items-end">
        <div
          role="status"
          aria-live="polite"
          className="flex w-full flex-col items-center gap-2 sm:items-end"
        >
          {polite.map((item) => (
            <Toast
              key={item.id}
              title={item.title}
              description={item.description}
              tone={item.tone}
              onDismiss={() => dismiss(item.id)}
            />
          ))}
        </div>
        <div
          role="alert"
          aria-live="assertive"
          className="flex w-full flex-col items-center gap-2 sm:items-end"
        >
          {assertive.map((item) => (
            <Toast
              key={item.id}
              title={item.title}
              description={item.description}
              tone={item.tone}
              onDismiss={() => dismiss(item.id)}
            />
          ))}
        </div>
      </div>
    </ToastApiContext.Provider>
  );
}

export interface ToastProps {
  title: string;
  description?: string;
  tone?: ToastTone;
  /** 传了才渲染关闭按钮——独立展示时不希望出现死按钮。 */
  onDismiss?: () => void;
  className?: string;
}

const toneBox: Record<ToastTone, string> = {
  neutral: "border-border bg-surface",
  info: "border-info/40 bg-info-weak",
  success: "border-success/40 bg-success-weak",
  warning: "border-warning/40 bg-warning-weak",
  danger: "border-danger/40 bg-danger-weak",
};

const toneTitle: Record<ToastTone, string> = {
  neutral: "text-fg",
  info: "text-info",
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger",
};

export function Toast({
  title,
  description,
  tone = "neutral",
  onDismiss,
  className,
}: ToastProps) {
  return (
    <div
      className={cn(
        "pointer-events-auto flex w-full max-w-sm items-start gap-2 rounded-lg border p-3 shadow-lg",
        styles.toastEnter ?? "",
        toneBox[tone],
        className,
      )}
    >
      <div className="min-w-0 flex-1">
        <p className={cn("text-sm font-semibold", toneTitle[tone])}>{title}</p>
        {description ? <p className="mt-0.5 text-xs text-fg-muted">{description}</p> : null}
      </div>
      {onDismiss ? (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="关闭提示"
          className={cn(
            "grid size-11 shrink-0 place-items-center rounded-md",
            "text-fg-muted transition-[transform,background-color,color] duration-fast ease-out",
            "hover:bg-surface-muted hover:text-fg",
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-ring",
          )}
        >
          <CloseIcon />
        </button>
      ) : null}
    </div>
  );
}
