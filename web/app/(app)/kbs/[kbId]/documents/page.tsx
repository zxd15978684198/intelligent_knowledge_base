"use client";

import { useAuth } from "@/features/auth";
import { DocumentTable } from "@/features/documents/DocumentTable";
import { UploadPanel } from "@/features/documents/UploadPanel";
import { useKbId } from "../layout";

/**
 * 文档列表页（N7）。
 *
 * 版式由 (app) 的 AppShell（main 自带 px/py）与 [kbId]/layout.tsx（面包屑 + KB 名 + 分区导航）提供，
 * 这里只排内容；kbId 走 layout 导出的 useKbId()，不再各自解析路由。
 */
export default function DocumentsPage() {
  const kbId = useKbId();
  const { identity } = useAuth();
  const workspaceId = identity?.default_workspace_id ?? null;

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-[28px]/[32px] font-semibold tracking-tight leading-tight text-fg">文档</h1>
        <p className="max-w-[65ch] text-[13px] leading-relaxed text-fg-muted">
          上传后进入摄取队列：解析、切片、向量化、发布。上传面板负责跟踪作业，下面的表格只读列表接口。
        </p>
      </header>

      {workspaceId === null ? (
        <p
          className="max-w-[65ch] rounded-md border border-warning bg-warning-weak px-4 py-3 text-sm leading-relaxed text-fg"
          role="status"
        >
          还没有拿到工作空间身份，稍后再试；上传与列表都需要 workspace 作用域。
        </p>
      ) : (
        <>
          <UploadPanel kbId={kbId} workspaceId={workspaceId} />
          <DocumentTable kbId={kbId} workspaceId={workspaceId} />
        </>
      )}
    </div>
  );
}
