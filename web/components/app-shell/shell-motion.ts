import styles from "@/components/app-shell/shell-motion.module.css";

/**
 * 壳层 stagger 入场的取用口（计划 §1.4 I3）。
 *
 * 返回 className + 内联 animationDelay，调用方用 `cn(base, enter.className)`
 * 合并类名、`style={enter.style}` 合并延迟。步长 30ms（计划口径「间隔 30ms」）：
 * 侧栏按 Brand(0) → 导航项 1..n → 身份区 n+1 排队，抽屉同序。
 *
 * 动画本体在 shell-motion.module.css：只动 opacity/transform；
 * `prefers-reduced-motion` 下整条动画关闭（含延迟），globals.css 的全局降级不受影响。
 */
export function shellEnter(step: number): {
  className: string;
  style: { animationDelay: string };
} {
  const safeStep = Number.isFinite(step) && step > 0 ? Math.floor(step) : 0;
  return {
    // noUncheckedIndexedAccess 下索引签名是 string | undefined；运行时该类一定存在，
    // 测试环境（vitest stub 掉 CSS import）拿不到时退化为空串=不带动效，不炸断言。
    className: styles.enter ?? "",
    style: { animationDelay: `${safeStep * 30}ms` },
  };
}
