import type { RetrievedChunk } from "@/lib/contracts";

/**
 * 检索结果的纯展示函数。
 *
 * 单独成文件是为了让「排序 / 排名空值 / 降级原因」这些容易被写错的规则可以直接单测，
 * 不必把它们埋进 JSX 里。凡是后端没给的字段（比如 `char_start/char_end`）这里一律
 * 返回 `—` 或原值，绝不补一个看起来合理的数。
 */

/** 两路召回通道的中文名；未知通道保留原键，别把新通道显示成「未知」。 */
const CHANNEL_LABELS: Record<string, string> = {
  vector: "向量通道",
  keyword: "关键词通道",
  fulltext: "全文通道",
};

export function channelLabel(channel: string): string {
  return CHANNEL_LABELS[channel] ?? `通道「${channel}」`;
}

/**
 * `degraded` 里的原因码来自后端 `ErrorCode`（17 个）。
 *
 * 只翻译 UI 真的会遇到的几个：其余原样显示原因码。把不认识的原因码翻译成「未知错误」
 * 反而会掩盖信息——这一页就是给人排查检索问题用的。
 */
const REASON_LABELS: Record<string, string> = {
  DEPENDENCY_UNAVAILABLE: "依赖不可用",
  INTERNAL_ERROR: "服务内部错误",
  INVALID_REQUEST: "请求被该通道拒绝",
  RESOURCE_NOT_FOUND: "索引不存在",
  INDEX_INCOMPLETE: "索引未完成",
};

export function reasonLabel(reason: string): string {
  const key = reason.trim();
  if (key === "") return "未说明原因";
  return REASON_LABELS[key] ?? key;
}

const STRATEGY_LABELS: Record<string, string> = {
  baseline: "基线策略",
  candidate: "候选策略",
};

export function strategyLabel(strategy: string): string {
  return STRATEGY_LABELS[strategy] ?? "未知策略";
}

export interface DegradedEntry {
  channel: string;
  reason: string;
  label: string;
}

/** `degraded` → 稳定的、按键排序的条目数组（对象键序不保证，展示顺序要保证）。 */
export function degradedEntries(degraded: Record<string, string> | undefined): DegradedEntry[] {
  if (!degraded) return [];
  return Object.entries(degraded)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([channel, reason]) => ({
      channel,
      reason,
      label: channelLabel(channel),
    }));
}

/**
 * 按融合分数降序；同分时用 `chunk_ordinal`、再不行用 `chunk_id` 兜底，
 * 保证每次渲染顺序一致（否则 React key 与用例断言都会抖）。
 */
export function sortChunksByScore(results: readonly RetrievedChunk[]): RetrievedChunk[] {
  return [...results].sort((left, right) => {
    if (left.rrf_score !== right.rrf_score) return right.rrf_score - left.rrf_score;
    if (left.chunk_ordinal !== right.chunk_ordinal) return left.chunk_ordinal - right.chunk_ordinal;
    return left.chunk_id.localeCompare(right.chunk_id);
  });
}

/** RRF 分数是 1/(k+rank) 量级，固定 6 位小数才看得见差异。 */
export function formatScore(score: number | null | undefined): string {
  if (score === null || score === undefined || !Number.isFinite(score)) return "—";
  return score.toFixed(6);
}

/** 单路排名；`null` 表示该通道没有召回这一条，显示 `—`（不是 0）。 */
export function formatRank(rank: number | null | undefined): string {
  if (rank === null || rank === undefined) return "—";
  return `#${rank}`;
}

export function formatPageRange(
  pageNo: number | null | undefined,
  pageEndNo: number | null | undefined,
): string {
  if (pageNo === null || pageNo === undefined) return "—";
  if (pageEndNo === null || pageEndNo === undefined || pageEndNo === pageNo) {
    return `第 ${pageNo} 页`;
  }
  return `第 ${pageNo}–${pageEndNo} 页`;
}

export function formatHeadingPath(headingPath: string | null | undefined): string {
  const trimmed = headingPath?.trim() ?? "";
  return trimmed === "" ? "—" : trimmed;
}

/** 表格摘要长度；展开后仍是完整 `content`，这里只压展示，不截断数据。 */
export const CONTENT_SUMMARY_LENGTH = 160;

export function summarizeContent(content: string, limit = CONTENT_SUMMARY_LENGTH): string {
  const normalized = content.replace(/\s+/g, " ").trim();
  if (normalized.length <= limit) return normalized;
  return `${normalized.slice(0, limit)}…`;
}

export interface ChunkFact {
  label: string;
  value: string;
}

/**
 * 展开区展示的「真实定位字段」。
 *
 * 检索响应里没有字符偏移，所以这里只列接口真的返回过的字段：想深挖就到切片列表接口
 * 按 `chunk_id` 取 `char_start/char_end`，不在本页伪造高亮。
 */
export function chunkFacts(chunk: RetrievedChunk): ChunkFact[] {
  return [
    { label: "切片 ID", value: chunk.chunk_id },
    { label: "文档 ID", value: chunk.document_id },
    { label: "生效文档版本 ID", value: chunk.active_document_version_id ?? "—" },
    { label: "切片序号", value: String(chunk.chunk_ordinal) },
    { label: "索引版本", value: String(chunk.index_version) },
    { label: "页码", value: formatPageRange(chunk.page_no, chunk.page_end_no) },
    { label: "章节路径", value: formatHeadingPath(chunk.heading_path) },
    { label: "定位引用", value: chunk.locator_ref ?? "—" },
  ];
}

/** 「共 N 条结果」；中文没有复数变化，单独抽出来只为统一文案。 */
export function describeResultCount(count: number): string {
  return count === 0 ? "没有命中任何切片" : `共 ${count} 条结果`;
}
