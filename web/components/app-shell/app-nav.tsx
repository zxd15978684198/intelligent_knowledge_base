"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";

import { isNavItemActive, NAV_ITEMS } from "@/components/app-shell/nav-items";
import { shellEnter } from "@/components/app-shell/shell-motion";
import { cn } from "@/lib/utils";

const ITEM_BASE =
  "group relative flex min-h-11 items-center gap-3 rounded-md px-3 text-sm transition-[background-color,color] duration-fast";
const ITEM_IDLE = "text-fg-muted hover:bg-surface-muted hover:text-fg";
const ITEM_ACTIVE = "bg-primary-weak font-medium text-primary";
/**
 * 当前路由的左侧指示条：3px 圆头短条 + primary-weak 圆角块底色（任务 N3-1 的
 * 「左侧指示条或圆角块」这里两者并用）。纯装饰，读屏仍以 `aria-current="page"` 为准。
 */
const ITEM_ACTIVE_BAR = "absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-full bg-primary";
/** 图标 hover 微位移：只动 transform（GPU），不碰布局属性。 */
const ITEM_ICON = "size-5 shrink-0 transition-transform duration-fast group-hover:translate-x-0.5";

/**
 * 主导航列表。桌面侧栏与窄屏抽屉共用同一个组件，因此两处的条目、顺序、高亮规则
 * 永远一致——「当前路由高亮一致性」就靠这一个组件保证，不许在别处另写 active 判定。
 *
 * 可访问性：
 * - 当前页用 `aria-current="page"` 标注，读屏会念"当前页面"，不靠背景色区分；
 * - 外链补 `sr-only` 文案说明"在新标签页打开"，并在视觉上带外链图标；
 * - 最小高度 44px（`min-h-11`），满足触控目标下限；
 * - `:focus-visible` 焦点环由 globals.css 统一提供，这里不加 outline-none。
 *
 * 入场动效（I3）：条目按 30ms 间隔 stagger（Brand 占 0 档，条目从 1 档起），
 * 只动 opacity/transform，`prefers-reduced-motion` 下自动关闭。
 *
 * `onNavigate` 由抽屉传入用于"点完就关"；侧栏不传，因此点击行为完全交给路由。
 */
export function AppNav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();

  return (
    <nav aria-label="主导航" className="mt-6 px-2">
      <p className="px-3 text-[11px] font-medium tracking-[0.08em] text-fg-subtle">导航</p>
      <ul role="list" className="mt-2 space-y-1">
        {NAV_ITEMS.map((item, index) => {
          const IconComponent = item.icon;
          const enter = shellEnter(index + 1);

          if (item.external === true) {
            return (
              <li key={item.href} className={enter.className} style={enter.style}>
                <a
                  href={item.href}
                  target="_blank"
                  rel="noreferrer"
                  onClick={onNavigate}
                  title={item.hint}
                  className={cn(ITEM_BASE, ITEM_IDLE)}
                >
                  <IconComponent aria-hidden="true" className={ITEM_ICON} />
                  <span className="truncate">{item.label}</span>
                  <span className="sr-only">（在新标签页打开）</span>
                </a>
              </li>
            );
          }

          const active = isNavItemActive(item, pathname);

          return (
            <li key={item.href} className={enter.className} style={enter.style}>
              <Link
                href={item.href}
                onClick={onNavigate}
                aria-current={active ? "page" : undefined}
                title={item.hint}
                className={cn(ITEM_BASE, active ? ITEM_ACTIVE : ITEM_IDLE)}
              >
                {active ? <span aria-hidden="true" className={ITEM_ACTIVE_BAR} /> : null}
                <IconComponent aria-hidden="true" className={ITEM_ICON} />
                <span className="truncate">{item.label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
