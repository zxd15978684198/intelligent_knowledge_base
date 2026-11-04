import type { ReactNode } from "react";

import { describePermission } from "@/lib/auth/permissions";
import { cn } from "@/lib/utils";

export interface DisabledReasonProps {
  /** 缺少的权限点，例如 `document.write`。 */
  permission: string;
  /** 追加说明，例如"请联系管理员申请编辑权限"。 */
  children?: ReactNode;
  className?: string;
}

/**
 * "为什么这里不可用"的统一文案。
 *
 * 只解释**界面**为何不可用，不做授权判断：真正的拒绝永远发生在服务端
 * （`src/pyknora/api/deps.py` 是唯一入口）。把这句话当授权检查用，就等着越权。
 */
export function DisabledReason({ permission, children, className }: DisabledReasonProps) {
  return (
    <p
      role="note"
      className={cn("flex items-start gap-1.5 text-sm leading-relaxed text-fg-muted", className)}
    >
      <span aria-hidden="true" className="mt-2 inline-block size-1.5 shrink-0 rounded-full bg-fg-subtle" />
      <span>
        需要「{describePermission(permission)}」权限（
        <code className="rounded-sm bg-surface-muted px-1 py-0.5 font-mono text-xs">{permission}</code>
        ）。{children}
      </span>
    </p>
  );
}
