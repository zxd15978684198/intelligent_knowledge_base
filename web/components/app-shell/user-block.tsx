import { SignOutIcon } from "@phosphor-icons/react";

import type { IdentityView } from "@/components/app-shell/identity-view";
import { shellEnter } from "@/components/app-shell/shell-motion";
import { cn, shortId } from "@/lib/utils";

/**
 * 壳层底部的身份区：当前用户、当前工作区、登出。
 *
 * 全站只有这一处展示身份，数据来自 `useAuth().identity`（N5 提供），不再额外请求
 * `/auth/me`——令牌签发时后端已经把 principal 与 workspace 一起回了。
 *
 * 用 `mt-auto` 钉在侧栏底部：导航再长也不会把它挤走；`min-h-11` 保证登出按钮够大。
 */
export function UserBlock({
  identity,
  onLogout,
  onNavigate,
}: {
  identity: IdentityView;
  onLogout: () => void;
  onNavigate?: () => void;
}) {
  const enter = shellEnter(4);

  return (
    <div
      className={cn("mt-auto border-t border-border px-3 pb-3 pt-4", enter.className)}
      style={enter.style}
    >
      <p className="text-[11px] font-medium tracking-[0.08em] text-fg-subtle">当前用户</p>
      <p className="mt-1 truncate text-sm font-medium text-fg" title={identity.displayName}>
        {identity.displayName}
      </p>
      <p className="mt-2 flex min-w-0 items-center gap-1.5 text-xs text-fg-muted">
        <span className="shrink-0">工作区</span>
        <code
          className="min-w-0 truncate rounded-sm bg-surface-muted px-1.5 py-0.5 text-fg tabular"
          title={identity.workspaceId ?? undefined}
        >
          {identity.workspaceId === null ? "未绑定" : shortId(identity.workspaceId)}
        </code>
      </p>
      <button
        type="button"
        onClick={() => {
          onLogout();
          onNavigate?.();
        }}
        className="mt-3 flex min-h-11 w-full items-center gap-2 rounded-md border border-border px-3 text-sm text-fg-muted transition-[background-color,color,transform] duration-fast hover:bg-surface-muted hover:text-fg active:scale-[0.98]"
      >
        <SignOutIcon aria-hidden="true" className="size-4 shrink-0" />
        退出登录
      </button>
    </div>
  );
}
