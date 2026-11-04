import { cn } from "@/lib/utils";

/**
 * 应用壳骨架屏：`AuthGuard` 在 `loading`（读令牌/验活）与 `anonymous`（跳转中）时渲染。
 *
 * 结构刻意与真实壳同构（左栏 + 顶栏 + 内容区），从骨架切到真实页面时不会整页重排；
 * 这也是"刷新时不闪登录页"的实现方式——用户看到的始终是工作台的形状。
 *
 * 可访问性：容器标 `aria-busy`，只有那段 `sr-only` 文本进 `role="status"` 实时区，
 * 读屏听到的是"正在校验登录状态"，而不是一堆空方块被逐个朗读。
 */
function Bar({ className }: { className: string }) {
  return <div aria-hidden="true" className={cn("animate-pulse rounded-md bg-surface-muted", className)} />;
}

export function ShellSkeleton() {
  return (
    <div className="min-h-dvh bg-canvas" aria-busy="true">
      <span role="status" aria-live="polite" className="sr-only">
        正在校验登录状态…
      </span>
      <div className="lg:flex">
        <div className="hidden border-r border-border bg-surface p-3 lg:flex lg:h-dvh lg:w-64 lg:shrink-0 lg:flex-col lg:gap-2">
          <Bar className="h-8 w-32" />
          <div className="mt-6 space-y-2">
            <Bar className="h-11 w-full" />
            <Bar className="h-11 w-full" />
            <Bar className="h-11 w-full" />
          </div>
          <div className="mt-auto space-y-2">
            <Bar className="h-4 w-20" />
            <Bar className="h-11 w-full" />
          </div>
        </div>
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex min-h-14 items-center border-b border-border bg-surface px-3 lg:hidden">
            <Bar className="h-8 w-28" />
          </div>
          <div className="min-w-0 flex-1 space-y-4 px-4 py-6 lg:px-8 lg:py-8">
            <Bar className="h-7 w-48" />
            <Bar className="h-4 w-72 max-w-full" />
            <div className="space-y-2">
              <Bar className="h-16 w-full" />
              <Bar className="h-16 w-full" />
              <Bar className="h-16 w-full" />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
