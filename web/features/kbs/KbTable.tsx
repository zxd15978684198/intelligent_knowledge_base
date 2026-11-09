"use client";

import Link from "next/link";
import {
  Badge,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui";
import type { KbSummary } from "./api";
import { KbRowActions } from "./KbRowActions";
import type { KbWorkspaceId } from "./hooks";

/**
 * 知识库列表。
 *
 * 纯展示：数据、分页、错误都在页面（`app/(app)/kbs/page.tsx`）处理，这里只负责
 * "每个知识库一行"。列的含义是后端 schema 的直接映射——嵌入与切片参数在创建时固化，
 * 所以放在列表里而不是详情页，用户一眼能对比两个知识库的索引形态。
 *
 * 窄屏可发现性（计划 §1.3 L4 / DAG N7）：7 列表格包在 `Table` 的 `overflow-x-auto`
 * 容器里，窄档要横向滚动才能看到「进入 / 操作」。容器没有原生视觉线索，旧账因此把
 * 滚动容器**内** `<table>` 自身宽度（1117>1024、845>390）误判成"被裁"；实测校正后
 * 不卡片化、不重排，只在窄档补一行「左右滑动」提示（`xl:hidden`，宽档表格不溢出时隐藏）。
 */

/** 固定 zh-CN + 固定时区：SSR 与上游机时区不同也不会出现 hydration 文案不一致。 */
const timestampFormat = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "Asia/Shanghai",
});

function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return timestampFormat.format(date);
}

export interface KbTableProps {
  items: readonly KbSummary[];
  workspaceId?: KbWorkspaceId;
  /** 透传给 Table 的外层容器（默认已带横向滚动）。 */
  containerClassName?: string;
}

export function KbTable({ items, workspaceId = null, containerClassName }: KbTableProps) {
  return (
    <div className="flex flex-col gap-2">
      <p className="flex items-center gap-1.5 text-xs text-fg-muted xl:hidden">
        <span aria-hidden="true">↔</span>
        表格可左右滑动，查看「进入 / 操作」等右侧列
      </p>
      <Table containerClassName={containerClassName}>
        <TableHeader>
          <TableRow>
            <TableHead>知识库</TableHead>
            <TableHead>嵌入模型</TableHead>
            <TableHead className="text-right tabular">切片</TableHead>
            <TableHead className="text-right tabular">版本</TableHead>
            <TableHead className="text-right tabular">更新时间</TableHead>
            <TableHead>进入</TableHead>
            <TableHead className="text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((kb) => (
            <TableRow key={kb.kb_id}>
              <TableCell>
                <Link
                  href={`/kbs/${kb.kb_id}/documents`}
                  className="font-medium text-fg underline-offset-4 hover:text-primary hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-ring"
                >
                  {kb.name}
                </Link>
              </TableCell>
              <TableCell>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="neutral">{kb.profile.embedding_model_key}</Badge>
                  <span className="tabular text-xs text-fg-muted">{kb.profile.embedding_dim} 维</span>
                </div>
              </TableCell>
              <TableCell className="whitespace-nowrap text-right text-sm tabular text-fg-muted">
                {kb.profile.chunk_size}
                <span className="text-fg-subtle"> / 重叠 </span>
                {kb.profile.chunk_overlap}
              </TableCell>
              <TableCell className="whitespace-nowrap text-right text-sm tabular text-fg-muted">
                v{kb.row_version}
              </TableCell>
              <TableCell className="whitespace-nowrap text-right text-sm tabular text-fg-muted">
                <time dateTime={kb.updated_at}>{formatTimestamp(kb.updated_at)}</time>
              </TableCell>
              <TableCell>
                <KbEnterActions kb={kb} />
              </TableCell>
              <TableCell className="text-right">
                <KbRowActions kb={kb} workspaceId={workspaceId} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

/**
 * 每一行的「进入」入口。
 *
 * 为什么要有这一列：知识库名本身就是个链接，但**链接不是入口**——用户看到一行数据时
 * 不会去猜"名字能点"。上传、检索、问答是三个并列的常用动作，必须各自有名字可见的按钮，
 * 否则新用户的第一反应就是"这个系统只能建库不能传文件"。
 *
 * 用 `Link` + 按钮样式而不是 `Button onClick={router.push}`：保留"新标签页打开"、
 * "复制链接"和右键菜单，键盘 `Enter` 也走原生语义。
 */
function KbEnterActions({ kb }: { kb: KbSummary }) {
  const base = `/kbs/${kb.kb_id}`;
  const entries = [
    { href: `${base}/documents`, label: "文档", hint: "上传与查看该库的文档" },
    { href: `${base}/query`, label: "检索", hint: "不带模型地看切片命中" },
    { href: `${base}/chat`, label: "问答", hint: "检索 + 流式生成带引用的回答" },
  ] as const;

  return (
    <div className="flex items-center gap-1 whitespace-nowrap">
      {entries.map((entry) => (
        <Link
          key={entry.href}
          href={entry.href}
          aria-label={`${entry.label}「${kb.name}」：${entry.hint}`}
          className="inline-flex h-8 items-center rounded-md border border-border bg-surface px-2.5 text-sm text-fg transition-colors hover:border-primary/40 hover:text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-ring"
        >
          {entry.label}
        </Link>
      ))}
    </div>
  );
}
