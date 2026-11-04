"use client";

import { useParams } from "next/navigation";

import { EmptyState } from "@/components/ui";
import { useAuth } from "@/features/auth";
import { QueryConsole } from "@/features/query";

/**
 * `/kbs/[kbId]/query` —— 检索调试页。
 *
 * 这一层刻意薄：只做两件事，把路由参数与当前身份取出来，再交给 `QueryConsole`。
 * 所有状态与分支都在 `features/query/QueryConsole.tsx`，那里能用假接口单测；
 * 挂在 Next 运行时上的页面本身没什么可断言的东西。
 *
 * 工作区：`X-Workspace-ID` 是可选的（后端 `api/deps.py:76-84` 会回落到 JWT 里的
 * `default_workspace_id`），所以这里取当前身份的默认工作区；取不到就传 `null`，
 * 让后端决定，而不是硬编码一个工作区 ID。
 */
export default function QueryPage() {
  const params = useParams<{ kbId: string }>();
  const { identity } = useAuth();
  const kbId = typeof params.kbId === "string" ? params.kbId : "";

  if (kbId === "") {
    return (
      <EmptyState
        title="缺少知识库 ID"
        description="当前地址里没有可用的知识库 ID，无法发起检索。请从知识库列表进入。"
      />
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold text-fg">检索调试</h1>
        <p className="text-sm text-fg-muted">
          直接调用 <code className="font-mono text-xs">POST /api/v1/knowledge-bases/{"{kb_id}"}/query</code>
          ，同时查看融合分数与两路召回排名。
        </p>
      </header>
      <QueryConsole kbId={kbId} workspaceId={identity?.default_workspace_id ?? null} />
    </div>
  );
}
