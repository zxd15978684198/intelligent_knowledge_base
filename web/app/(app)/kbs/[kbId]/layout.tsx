"use client";

import { createContext, useContext, type ReactNode } from "react";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { Badge, Button, EmptyState, Skeleton } from "@/components/ui";
import { useAuth } from "@/features/auth";
import { describeKbError } from "@/features/kbs/errors";
import { useKb } from "@/features/kbs/hooks";
import { cn } from "@/lib/utils";

/**
 * 知识库详情的外壳（客户端布局）。
 *
 * 职责：解析 `params.kbId` → 拉一次 KB 头（列表页已把详情键挂在列表键下，通常直接命中缓存）
 * → 渲染面包屑、KB 名与二级导航，并把 `kbId` 通过 context 交给子页面。
 * 子页面拿 id 只走 `useKbId()`，不再各自解析路由，避免"两处解析、一处漏了数组兜底"。
 *
 * 外层 `(app)` 壳已经渲染了 `<main id="main-content">` 与页面内边距，这里只用 `<div>`，
 * 不能再套一个 `<main>`（重复地标 + 无效 HTML）。
 */

export const KbContext = createContext<string | null>(null);

export function useKbId(): string {
  const kbId = useContext(KbContext);
  if (kbId === null) {
    throw new Error("useKbId() 必须在 /kbs/[kbId] 布局内使用：缺少 KbContext.Provider。");
  }
  return kbId;
}

const SECTIONS = [
  { segment: "documents", label: "文档" },
  { segment: "query", label: "检索" },
  { segment: "chat", label: "问答" },
] as const;

function isSectionActive(pathname: string, base: string): boolean {
  return pathname === base || pathname.startsWith(`${base}/`);
}

export default function KbLayout({ children }: { children: ReactNode }) {
  const params = useParams<{ kbId?: string | string[] }>();
  const pathname = usePathname();
  const { identity } = useAuth();
  const workspaceId = identity?.default_workspace_id ?? null;

  const rawKbId = params.kbId;
  const kbId = Array.isArray(rawKbId) ? (rawKbId[0] ?? "") : (rawKbId ?? "");

  const kbQuery = useKb(kbId, { workspaceId, enabled: kbId.length > 0 });
  const kb = kbQuery.data ?? null;

  if (kbId.length === 0) {
    return (
      <EmptyState
        title="地址里缺少知识库 ID"
        description="无法确定要打开哪个知识库，请从知识库列表重新进入。"
        action={
          <Button asChild variant="secondary">
            <Link href="/kbs">返回知识库列表</Link>
          </Button>
        }
      />
    );
  }

  return (
    <KbContext.Provider value={kbId}>
      <div className="flex flex-col gap-6">
        <header className="flex flex-col gap-4 border-b border-border pb-4">
          <nav aria-label="面包屑" className="text-sm">
            <ol className="flex flex-wrap items-center gap-1.5 text-fg-muted">
              <li>
                <Link
                  href="/kbs"
                  className="rounded-sm underline-offset-4 hover:text-fg hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
                >
                  全部知识库
                </Link>
              </li>
              <li aria-hidden="true" className="text-fg-subtle">
                /
              </li>
              <li className="min-w-0 truncate font-medium text-fg">
                {kb ? kb.name : <Skeleton className="inline-block h-4 w-32 align-middle" />}
              </li>
            </ol>
          </nav>

          <div className="flex flex-wrap items-center gap-2">
            {/* 这里只是"当前知识库"的上下文，不占标题层级：二级页面自己用 `<h1>`，
                否则会出现两个 h1，或 h2 排在 h1 之前。 */}
            <p className="min-w-0 truncate text-xl font-semibold text-fg">
              {kb ? kb.name : "知识库"}
            </p>
            {kb ? (
              <>
                <Badge tone="neutral">{kb.profile.embedding_model_key}</Badge>
                <span className="text-xs text-fg-muted">
                  {kb.profile.embedding_dim} 维 · 切片 {kb.profile.chunk_size}/重叠{" "}
                  {kb.profile.chunk_overlap} · v{kb.row_version}
                </span>
              </>
            ) : null}
          </div>

          {kbQuery.isError ? (
            <div
              role="alert"
              className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-surface-muted px-3 py-2 text-sm text-danger"
            >
              <span>{describeKbError(kbQuery.error, "load")}</span>
              <Button variant="secondary" size="sm" onClick={() => void kbQuery.refetch()}>
                重试
              </Button>
            </div>
          ) : null}

          <nav aria-label="知识库内导航">
            <ul className="flex flex-wrap gap-1">
              {SECTIONS.map((section) => {
                const href = `/kbs/${kbId}/${section.segment}`;
                const active = isSectionActive(pathname, href);
                return (
                  <li key={section.segment}>
                    <Link
                      href={href}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "-mb-px inline-flex items-center border-b-2 px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40",
                        active
                          ? "border-primary text-fg"
                          : "border-transparent text-fg-muted hover:border-border hover:text-fg",
                      )}
                    >
                      {section.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
        </header>

        <div>{children}</div>
      </div>
    </KbContext.Provider>
  );
}
