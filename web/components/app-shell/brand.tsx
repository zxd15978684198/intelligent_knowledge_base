import { cn } from "@/lib/utils";

/**
 * 产品标识：字标 + 产品名 + 定位副标题。
 *
 * 尺度按计划 §6 的"工具界面"来：15px 产品名，不做营销页大标题。方块字标用纯色
 * `bg-primary`，不用渐变/玻璃拟态（D6 明确排除）。
 */
export function Brand({ className }: { className?: string }) {
  return (
    <div className={cn("flex min-w-0 items-center gap-2.5", className)}>
      <span
        aria-hidden="true"
        className="flex size-8 shrink-0 items-center justify-center rounded-md bg-primary text-sm font-semibold text-primary-fg"
      >
        P
      </span>
      <span className="min-w-0">
        <span className="block truncate text-sm font-semibold tracking-tight text-fg">PyKnora</span>
        <span className="block truncate text-xs text-fg-muted">证据工作台</span>
      </span>
    </div>
  );
}
