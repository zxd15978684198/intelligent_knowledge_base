"use client";

import { useParams } from "next/navigation";

import { ChatScreen } from "@/features/chat";

/**
 * `/kbs/[kbId]/chat` —— 流式问答页。
 *
 * 本文件只做一件事：把路由参数交给 `ChatScreen`。所有状态机、传输与布局都在
 * `@/features/chat` 里，这样单测可以直接渲染 `ChatScreen`，不必伪造 Next 的
 * 路由上下文（`useParams()` 是唯一的路由依赖，且它只在浏览器里跑）。
 *
 * `key={kbId}` 是刻意的：切知识库时整块组件重新挂载 → 卸载清理会 abort 在途请求，
 * 上一轮的会话状态也随组件一起丢弃（不跨库串答案、不自动重放）。
 */
export default function ChatPage() {
  const params = useParams();
  const raw = params["kbId"];
  const kbId = Array.isArray(raw) ? (raw[0] ?? "") : (raw ?? "");

  if (kbId === "") {
    return (
      <div className="rounded-lg border border-dashed border-border bg-surface px-4 py-10 text-center">
        <p className="text-sm font-medium text-fg">缺少知识库参数</p>
        <p className="mt-1 text-xs text-fg-muted">请从知识库列表进入问答。</p>
      </div>
    );
  }

  return <ChatScreen key={kbId} kbId={kbId} />;
}
