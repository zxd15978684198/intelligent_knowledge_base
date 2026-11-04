/**
 * `@/features/query` 公共入口（N8）。
 *
 * 稳定面（改了要通知依赖方）：`runQuery` / `QueryConsole`，以及各组件 props。
 * `format.ts` 的纯函数也导出：后续命中可视化、引用面板要复用同一套排名与分数口径，
 * 各写一份必然漂移。
 */
export {
  QUERY_DEFAULT_TOP_K,
  QUERY_MAX_LENGTH,
  QUERY_MAX_TOP_K,
  QUERY_MIN_TOP_K,
  TOP_K_OPTIONS,
  QueryValidationError,
  isQueryValidationError,
  normalizeQueryInput,
  queryEndpoint,
  runQuery,
} from "./api";
export type {
  NormalizedRunQueryInput,
  QueryValidationField,
  RunQueryInput,
} from "./api";

export { QueryConsole } from "./QueryConsole";
export type { QueryConsoleProps } from "./QueryConsole";

export { QueryComposer, EXAMPLE_QUERIES } from "./QueryComposer";
export type { QueryComposerProps } from "./QueryComposer";

export { RetrievalTable } from "./RetrievalTable";
export type { RetrievalTableProps } from "./RetrievalTable";

export { DegradedBanner } from "./DegradedBanner";
export type { DegradedBannerProps } from "./DegradedBanner";

export { StrategyBadge } from "./StrategyBadge";
export type { StrategyBadgeProps } from "./StrategyBadge";

export { describeQueryError } from "./errors";
export type { QueryErrorView } from "./errors";

export {
  chunkFacts,
  degradedEntries,
  describeResultCount,
  formatHeadingPath,
  formatPageRange,
  formatRank,
  formatScore,
  reasonLabel,
  sortChunksByScore,
  strategyLabel,
  summarizeContent,
} from "./format";
export type { ChunkFact, DegradedEntry } from "./format";
