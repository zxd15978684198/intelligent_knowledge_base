/**
 * 跳过导航链接：键盘用户按第一个 Tab 就能越过侧栏直达 `<main id="main-content">`。
 *
 * 为什么不用 `sr-only` + `focus:not-sr-only`：`sr-only` 把元素设成
 * `position: absolute`，聚焦时再切回 `static`；两个 position 工具类同时命中时谁生效
 * 取决于 Tailwind 的输出顺序，一旦 `static` 赢，链接会插进正常流里顶开页面布局。
 * 这里的做法不依赖工具类优先级：位置永远 `fixed` 在左上，只切换位移把元素移出视口。
 */
export function SkipLink() {
  return (
    <a
      href="#main-content"
      className="fixed left-4 top-4 z-50 -translate-y-[200%] rounded-md border border-border-strong bg-surface px-4 py-2 text-sm font-medium text-fg shadow-md transition-transform duration-fast focus:translate-y-0"
    >
      跳到主要内容
    </a>
  );
}
