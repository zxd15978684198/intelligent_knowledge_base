"use client";

import * as React from "react";

import { Button, Field, Select, Textarea } from "@/components/ui";

import {
  QUERY_DEFAULT_TOP_K,
  QUERY_MAX_LENGTH,
  QueryValidationError,
  TOP_K_OPTIONS,
  normalizeQueryInput,
  type NormalizedRunQueryInput,
} from "./api";

/**
 * 检索输入区。
 *
 * 交互口径（与计划 §4 的检索组件一致）：
 * - `Cmd/Ctrl + Enter` 提交——调试时手不离键盘，不该强迫鼠标去找按钮；
 * - top_k 只给 5/10/20/50 四档，其中 10 是后端默认值；越界值由 `normalizeQueryInput`
 *   拦下并给出具体原因（不静默改成合法值）；
 * - 提交中禁用输入与按钮：一次只跑一个检索，避免两次响应交叉覆盖结果；
 * - 空查询不发请求：本地就拦，省一次注定 422 的往返。
 *
 * 组件本身不发请求（`onSubmit` 由外层给），这样它可以脱离网络单测。
 */
export interface QueryComposerProps {
  /** 有请求在飞：输入、档位与按钮全部禁用。 */
  pending: boolean;
  onSubmit: (input: NormalizedRunQueryInput) => void;
}

/** 示例问题只填进输入框、不直接发请求：让人先改一改再跑，免得一点就出一次调用。 */
export const EXAMPLE_QUERIES: readonly string[] = [
  "上传文档后多久可以被检索到？",
  "支持哪些文档格式？",
  "向量检索和关键词检索是怎么融合排序的？",
];

export function QueryComposer({ pending, onSubmit }: QueryComposerProps) {
  const fieldId = React.useId();
  const topKId = `${fieldId}-topk`;
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);
  const [query, setQuery] = React.useState("");
  const [topK, setTopK] = React.useState<number>(QUERY_DEFAULT_TOP_K);
  const [error, setError] = React.useState<string | null>(null);

  const submit = React.useCallback(() => {
    if (pending) return;
    try {
      const normalized = normalizeQueryInput({ query, top_k: topK });
      setError(null);
      setQuery(normalized.query);
      onSubmit(normalized);
    } catch (caught) {
      setError(caught instanceof QueryValidationError ? caught.message : "提交失败，请重试。");
      textareaRef.current?.focus();
    }
  }, [onSubmit, pending, query, topK]);

  const handleKeyDown = React.useCallback(
    (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        event.preventDefault();
        submit();
      }
    },
    [submit],
  );

  const fillExample = React.useCallback((example: string) => {
    if (pending) return;
    setQuery(example);
    setError(null);
    textareaRef.current?.focus();
  }, [pending]);

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <Field
        id={fieldId}
        label="检索问题"
        hint={`最多 ${QUERY_MAX_LENGTH} 个字符；⌘/Ctrl + Enter 提交。`}
        error={error ?? undefined}
      >
        <Textarea
          ref={textareaRef}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="例如：向量检索和关键词检索是怎么融合排序的？"
          disabled={pending}
          rows={5}
        />
      </Field>

      <Field id={topKId} label="返回条数（top_k）" hint={`上限 50；越界会被本地拦截，不会静默截断。`}>
        <Select
          value={String(topK)}
          onChange={(event) => {
            const parsed = Number(event.target.value);
            if (Number.isInteger(parsed)) setTopK(parsed);
          }}
          disabled={pending}
        >
          {TOP_K_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </Select>
      </Field>

      <Button type="submit" loading={pending} disabled={pending}>
        {pending ? "检索中…" : "运行检索"}
      </Button>

      <div className="flex flex-col gap-2">
        <p className="text-xs font-semibold text-fg-muted" id={`${fieldId}-examples`}>
          示例问题（点击填入，不会自动提交）
        </p>
        <div className="flex flex-wrap gap-2" role="group" aria-labelledby={`${fieldId}-examples`}>
          {EXAMPLE_QUERIES.map((example) => (
            <Button
              key={example}
              type="button"
              variant="ghost"
              size="sm"
              disabled={pending}
              onClick={() => fillExample(example)}
            >
              {example}
            </Button>
          ))}
        </div>
      </div>
    </form>
  );
}
