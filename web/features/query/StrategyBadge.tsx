import { Badge, type BadgeTone } from "@/components/ui";
import { cn } from "@/lib/utils";

import { strategyLabel } from "./format";

/**
 * `retrieval_strategy` 徽标。
 *
 * 后端这个字段是裸 `str`（`schemas/query.py:105` 注明别收窄），由服务端环境变量决定：
 * 客户端**不能**切换，也不该假装能切换。所以这里只显示「现在跑的是哪套策略」，
 * 并把「切策略要改服务端配置并重启 API」写进 tooltip 和读屏文本——否则用户会点着
 * 徽标找切换按钮。
 *
 * 未知取值原样展示：将来后端加了第三种策略，这个组件不该把它藏起来。
 */
const STRATEGY_HINT = "由服务端环境变量决定，切换需重启 API。";

export interface StrategyBadgeProps {
  strategy: string;
  className?: string;
}

const STRATEGY_TONES: Record<string, BadgeTone> = {
  baseline: "info",
  candidate: "warning",
};

export function StrategyBadge({ strategy, className }: StrategyBadgeProps) {
  const tone = STRATEGY_TONES[strategy] ?? "neutral";

  return (
    <span
      className={cn("inline-flex flex-wrap items-center gap-1.5", className)}
      title={STRATEGY_HINT}
      data-testid="strategy-badge"
    >
      <Badge tone={tone}>策略：{strategyLabel(strategy)}</Badge>
      <code className="font-mono text-xs text-fg-subtle">{strategy}</code>
      <span className="sr-only">{STRATEGY_HINT}</span>
    </span>
  );
}
