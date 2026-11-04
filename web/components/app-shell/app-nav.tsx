"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";

import { isNavItemActive, NAV_ITEMS } from "@/components/app-shell/nav-items";
import { cn } from "@/lib/utils";

const ITEM_BASE =
  "flex min-h-11 items-center gap-3 rounded-md px-3 text-sm transition-colors duration-150";
const ITEM_IDLE = "text-fg-muted hover:bg-surface-muted hover:text-fg";
const ITEM_ACTIVE = "bg-primary-weak font-medium text-primary";

/**
 * 主导航列表。桌面侧栏与窄屏抽屉共用同一个组件，因此两处的条目、顺序、高亮规则
 * 永远一致。
 *
 * 可访问性：
 * - 当前页用 `aria-current="page"` 标注，读屏会念"当前页面"，不靠背景色区分；
 * - 外链补 `sr-only` 文案说明"在新标签页打开"，并在视觉上带外链图标；
 * - 最小高度 44px（`min-h-11`），满足触控目标下限。
 *
 * `onNavigate` 由抽屉传入用于"点完就关"；侧栏不传，因此点击行为完全交给路由。
 */
export function AppNav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();

  return (
    <nav aria-label="主导航" className="mt-6 px-2">
      <p className="px-3 text-[11px] font-medium tracking-[0.08em] text-fg-subtle">导航</p>
      <ul role="list" className="mt-2 space-y-1">
        {NAV_ITEMS.map((item) => {
          const IconComponent = item.icon;

          if (item.external === true) {
            return (
              <li key={item.href}>
                <a
                  href={item.href}
                  target="_blank"
                  rel="noreferrer"
                  onClick={onNavigate}
                  title={item.hint}
                  className={cn(ITEM_BASE, ITEM_IDLE)}
                >
                  <IconComponent aria-hidden="true" className="size-5 shrink-0" />
                  <span className="truncate">{item.label}</span>
                  <span className="sr-only">（在新标签页打开）</span>
                </a>
              </li>
            );
          }

          const active = isNavItemActive(item, pathname);

          return (
            <li key={item.href}>
              <Link
                href={item.href}
                onClick={onNavigate}
                aria-current={active ? "page" : undefined}
                title={item.hint}
                className={cn(ITEM_BASE, active ? ITEM_ACTIVE : ITEM_IDLE)}
              >
                <IconComponent aria-hidden="true" className="size-5 shrink-0" />
                <span className="truncate">{item.label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
