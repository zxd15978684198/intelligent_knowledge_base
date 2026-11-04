"use client";

import * as React from "react";

/**
 * 是否处于宽屏（默认 `min-width: 80rem`，与 Tailwind 的 `xl` 一致）。
 *
 * 为什么用 JS 而不是纯 CSS 断点：窄屏要求"证据栏折叠成 Tabs"，那意味着**答案与证据
 * 互斥显示**，而宽屏是并排。用 CSS 两份 DOM 各渲染一遍会带来两个真实问题：
 * 1. `StreamingAnswer` 里的 `markerRefs` 会被注册两份，`focus()` 可能落到隐藏副本上；
 * 2. 同一个 `aria-labelledby` id 在页面上出现两次，读屏读哪个都不对。
 * 所以按模式二选一渲染，state 都在 reducer 里，切换模式不会丢内容。
 *
 * 初值取 `false`（窄屏、Tabs）：服务端不知道视口宽度，先渲染窄屏版本可以避免
 * "宽屏元素闪一下再收起来"；`matchMedia` 在挂载后立刻纠正。
 */
export function useWideLayout(query = "(min-width: 80rem)"): boolean {
  const [wide, setWide] = React.useState(false);

  React.useEffect(() => {
    const mediaQuery = window.matchMedia(query);
    const update = () => {
      setWide(mediaQuery.matches);
    };
    update();
    mediaQuery.addEventListener("change", update);
    return () => {
      mediaQuery.removeEventListener("change", update);
    };
  }, [query]);

  return wide;
}
