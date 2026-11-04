/**
 * 引用定位与越界判定 —— 纯函数，无 React、无 DOM。
 *
 * 后端只把"越界条数"发给前端（`done.invalid_citations` 是 `len(invalid)`），
 * 具体是哪几个序号必须前端按同一套规则反算。所以这里的正则与判定口径必须与
 * `src/pyknora/services/rag/answer.py` 的 `_CITATION_MARKER` / `invalid_cited_indices`
 * 完全一致：
 *
 *   _CITATION_MARKER = re.compile(r"\[资料\s*(\d+)\]")
 *   invalid = {i for i in cited if i < 1 or i > context_size}
 *
 * 序号是**1 起**的（模型看到的是 `[资料 1]` 打头），而 `citations` 数组是 0 起，
 * 所以渲染第 N 条引用要取 `citations[N - 1]`。这一处 off-by-one 是这段代码唯一
 * 容易出错的地方，因此所有换算都收在本文件里，组件不许自己 +1/-1。
 */

/** 与后端 `_CITATION_MARKER` 同源。带 `g` 的正则会记住 `lastIndex`，所以每次现建。 */
const MARKER_SOURCE = "\\[资料\\s*(\\d+)\\]";

function newMarkerPattern(): RegExp {
  return new RegExp(MARKER_SOURCE, "g");
}

/** 渲染用片段：普通文本，或一个 `[资料 N]` 标记。 */
export type AnswerSegment =
  | { readonly kind: "text"; readonly text: string }
  | { readonly kind: "citation"; readonly index: number; readonly raw: string };

/** 1 起的序号是否落在本次投喂的资料条数内。 */
export function isCitationIndexInRange(index: number, contextSize: number): boolean {
  return Number.isInteger(index) && index >= 1 && index <= contextSize;
}

/**
 * 把回答正文切成"文本 / 引用标记"片段，供逐段渲染。
 *
 * 保留 `raw`（原始 `[资料 3]`）是为了让界面能原样显示模型写下的标记，而不是
 * 重排成一个"看起来更整齐"的编号——正文已经被发出去了，前端没有资格改它。
 */
export function segmentAnswer(answer: string): AnswerSegment[] {
  const segments: AnswerSegment[] = [];
  let cursor = 0;

  for (const match of answer.matchAll(newMarkerPattern())) {
    const start = match.index;
    const raw = match[0];
    const digits = match[1];
    if (digits === undefined) {
      continue;
    }
    if (start > cursor) {
      segments.push({ kind: "text", text: answer.slice(cursor, start) });
    }
    segments.push({ kind: "citation", index: Number.parseInt(digits, 10), raw });
    cursor = start + raw.length;
  }

  if (cursor < answer.length) {
    segments.push({ kind: "text", text: answer.slice(cursor) });
  }
  return segments;
}

/** 回答里出现过的序号（升序去重）。 */
export function citedIndices(answer: string): number[] {
  const seen = new Set<number>();
  for (const match of answer.matchAll(newMarkerPattern())) {
    const digits = match[1];
    if (digits !== undefined) {
      seen.add(Number.parseInt(digits, 10));
    }
  }
  return [...seen].sort((left, right) => left - right);
}

/**
 * 序号越界的引用：模型编了不存在的资料，或引用了被 `chat_max_context_chunks`
 * 截掉的那几条。与后端 `invalid_cited_indices` 同口径。
 */
export function invalidCitedIndices(answer: string, contextSize: number): number[] {
  return citedIndices(answer).filter((index) => !isCitationIndexInRange(index, contextSize));
}

/** 把 1 起序号换成数组下标；越界返回 `null`（调用方必须处理，不许静默 -1）。 */
export function citationForIndex<T>(citations: readonly T[], index: number): T | null {
  if (!isCitationIndexInRange(index, citations.length)) {
    return null;
  }
  return citations[index - 1] ?? null;
}

/** 页码展示：`3` / `3–4` / `无页码`。两个字段都可能为 `null`（txt/md）。 */
export function formatPageRange(pageNo: number | null, pageEndNo: number | null): string {
  if (pageNo === null && pageEndNo === null) {
    return "无页码";
  }
  if (pageNo === null) {
    return `第 ${String(pageEndNo)} 页`;
  }
  if (pageEndNo === null || pageEndNo === pageNo) {
    return `第 ${String(pageNo)} 页`;
  }
  return `第 ${String(pageNo)}–${String(pageEndNo)} 页`;
}

/** 证据栏的一行：真实引用，或"模型引用了不存在的资料"的幽灵行。 */
export interface CitationRow<T> {
  /** 1 起序号，与正文里的 `[资料 N]` 对齐。 */
  readonly index: number;
  /** 本次检索真实返回的引用；`null` 表示越界（后端只给了条数，没有对象）。 */
  readonly citation: T | null;
  readonly invalid: boolean;
  /** 正文是否真的引用了它（没被引用的资料只是"检索到了"，不是"用到了"）。 */
  readonly citedInAnswer: boolean;
}

/**
 * 把"引用数组 + 越界序号 + 正文里出现过的序号"合成证据栏的行。
 *
 * 为什么要给越界序号也出行：模型写了 `[资料 7]` 而本次只喂了 3 条时，用户必须能在
 * 证据栏里看到这一行被标红——只丢一句话"有 1 条引用未通过校验"却点不到任何东西，
 * 等于让用户自己数。行按序号升序，幽灵行与真实行混排，读起来与正文一致。
 */
export function buildCitationRows<T>(
  citations: readonly T[],
  invalidIndices: readonly number[],
  cited: readonly number[],
): CitationRow<T>[] {
  const citedSet = new Set(cited);
  const rows: CitationRow<T>[] = citations.map((citation, offset) => {
    const index = offset + 1;
    return {
      index,
      citation,
      invalid: !isCitationIndexInRange(index, citations.length),
      citedInAnswer: citedSet.has(index),
    };
  });

  for (const index of invalidIndices) {
    if (isCitationIndexInRange(index, citations.length)) {
      continue;
    }
    rows.push({ index, citation: null, invalid: true, citedInAnswer: true });
  }

  return rows.sort((left, right) => left.index - right.index);
}

