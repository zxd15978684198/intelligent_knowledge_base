"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { invalidateSession } from "@/lib/auth/session";

import { ChatComposer } from "./ChatComposer";
import { ChatTurn } from "./ChatTurn";
import { CitationRail } from "./CitationRail";
import { citedIndices } from "./citations";
import {
  chatSessionReducer,
  initialChatSession,
  isStreaming,
  turnForSelection,
  turnInvalidIndices,
} from "./conversation";
import { canStop, initialChatState, type ChatState } from "./machine";
import { streamChat, type StreamChatInput } from "./stream";
import { useWideLayout } from "./use-wide-layout";

/**
 * 问答页的可测试主体（`page.tsx` 只负责取路由参数）。
 *
 * 职责边界：
 * - **一轮一个状态机**：`chatSessionReducer` 把 `chatReducer` 套在每一轮上，整段会话
 *   因此只有一次 `useReducer`（动态数量的 hook 是写不出来的）。
 * - **同一时刻只允许一条流**：`abortRef` 既是"正在跑"的标志，也是中止句柄；
 *   发送前若已有流在跑就直接返回，不排队也不并发。
 * - **不自动重放**：无论失败、中止还是断流，都不自动重发。用户看得到发生了什么，
 *   要不要重试是他的决定——静默重放会让"预算中止/上游失败"的语义消失。
 * - **切换知识库/离开页面即中止**：`kbId` 变化时本组件被父级 `key` 重新挂载，
 *   卸载清理负责 abort；请求一断，服务端也就不用再往下生成。
 */
export interface ChatScreenProps {
  readonly kbId: string;
  readonly workspaceId?: string | null;
  /** 每轮取回多少条片段；省略时用后端默认值（6）。 */
  readonly topK?: number;
}

type NarrowTab = "answer" | "evidence";

export function ChatScreen({ kbId, workspaceId = null, topK }: ChatScreenProps): React.ReactElement {
  const [session, dispatch] = React.useReducer(chatSessionReducer, initialChatSession);
  const [tab, setTab] = React.useState<NarrowTab>("answer");
  const wide = useWideLayout();
  const abortRef = React.useRef<AbortController | null>(null);
  const turnSeqRef = React.useRef(0);

  const activeTurn = session.turns.find((turn) => turn.id === session.activeTurnId) ?? null;
  const activeState: ChatState = activeTurn?.state ?? initialChatState;
  const streaming = isStreaming(activeState) && canStop(activeState);
  const evidenceTurn = turnForSelection(session);
  const selection = session.selectedCitation;
  const evidenceCount = evidenceTurn?.state.citations.length ?? 0;

  const submit = React.useCallback(
    (query: string) => {
      // 只允许一条在跑：已经有一条时忽略新的提交（发送按钮此时也已变成"停止"）。
      if (abortRef.current !== null) {
        return;
      }
      turnSeqRef.current += 1;
      const id = `turn-${String(turnSeqRef.current)}`;
      const controller = new AbortController();
      abortRef.current = controller;
      dispatch({ type: "SUBMIT", id, query });
      setTab("answer");
      const input: StreamChatInput =
        topK === undefined
          ? { kbId, query, workspaceId }
          : { kbId, query, topK, workspaceId };
      void streamChat(
        input,
        (event) => {
          dispatch({ type: "EVENT", id, event });
        },
        controller.signal,
      ).finally(() => {
        if (abortRef.current === controller) {
          abortRef.current = null;
        }
      });
    },
    [kbId, topK, workspaceId],
  );

  const stop = React.useCallback(() => {
    const controller = abortRef.current;
    if (controller === null) {
      return;
    }
    abortRef.current = null;
    controller.abort();
    const id = session.activeTurnId;
    if (id !== null) {
      // 立刻把界面切到"已停止"，不等网络层回话（abort 的回调可能晚一拍）。
      dispatch({ type: "ABORT", id });
    }
  }, [session.activeTurnId]);

  const clear = React.useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    dispatch({ type: "CLEAR" });
    setTab("answer");
  }, []);

  // 卸载（含父级以 kbId 为 key 的重新挂载）时中止在途请求：别让服务端继续白算 token。
  React.useEffect(() => {
    return () => {
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, [kbId]);

  const selectCitation = React.useCallback(
    (turnId: string, index: number, source: NarrowTab) => {
      dispatch({ type: "SELECT_CITATION", turnId, index });
      if (!wide) {
        // 证据 → 正文：从侧栏点过来要能看到被标亮的那一处；
        // 正文 → 证据：从标记点过去要能读到引用的原文与页码。
        setTab(source);
      }
    },
    [wide],
  );

  // 401 只有一个合法处置：清登录态（`invalidateSession` 会清令牌并通知订阅者）。
  const unauthenticated = session.turns.some((turn) => turn.state.streamError?.status === 401);
  React.useEffect(() => {
    if (unauthenticated) {
      invalidateSession();
    }
  }, [unauthenticated]);

  const conversation =
    session.turns.length === 0 ? (
      <div className="rounded-lg border border-dashed border-border bg-surface px-4 py-10 text-center">
        <p className="text-sm font-medium text-fg">向当前知识库提问</p>
        <p className="mx-auto mt-1 max-w-md text-xs leading-relaxed text-fg-muted">
          回答按收到的 token 逐字渲染，不做打字机动画。多轮对话只保留在当前页面（后端没有会话接口），
          刷新即清空，也不会自动重放。
        </p>
      </div>
    ) : (
      <div className="space-y-4">
        {session.turns.map((turn) => (
          <ChatTurn
            key={turn.id}
            turn={turn}
            activeCitation={selection !== null && selection.turnId === turn.id ? selection.index : null}
            onPickCitation={(index) => {
              selectCitation(turn.id, index, "evidence");
            }}
          />
        ))}
      </div>
    );

  const evidence =
    evidenceTurn === null ? (
      <p className="rounded-md border border-dashed border-border px-3 py-4 text-xs text-fg-muted">
        提问后这里会列出本轮的引用片段、页码与检索降级信息。
      </p>
    ) : (
      <CitationRail
        citations={evidenceTurn.state.citations}
        invalidIndices={turnInvalidIndices(evidenceTurn)}
        invalidCount={evidenceTurn.state.done?.invalid_citations ?? 0}
        citedIndices={citedIndices(evidenceTurn.state.answer)}
        activeIndex={selection !== null && selection.turnId === evidenceTurn.id ? selection.index : null}
        onSelect={(index) => {
          selectCitation(evidenceTurn.id, index, "answer");
        }}
        streaming={isStreaming(evidenceTurn.state)}
      />
    );

  return (
    <div className="flex min-h-full flex-col gap-4">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h1 className="text-lg font-medium text-fg">问答</h1>
          <p className="text-xs text-fg-muted">
            基于本知识库检索并流式生成；引用与降级信息在证据栏（窄屏为页签）。
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={clear}
          disabled={session.turns.length === 0 && !streaming}
        >
          清空
        </Button>
      </header>

      {wide ? (
        <div className="grid flex-1 items-start gap-4 xl:grid-cols-[minmax(0,1fr)_20rem]">
          <div className="min-w-0">{conversation}</div>
          <aside aria-label="证据栏" className="xl:sticky xl:top-4">
            {evidence}
          </aside>
        </div>
      ) : (
        <Tabs
          value={tab}
          onValueChange={(value) => {
            setTab(value === "evidence" ? "evidence" : "answer");
          }}
          className="flex-1"
        >
          <TabsList>
            <TabsTrigger value="answer">回答</TabsTrigger>
            <TabsTrigger value="evidence">证据（{evidenceCount}）</TabsTrigger>
          </TabsList>
          <TabsContent value="answer">{conversation}</TabsContent>
          <TabsContent value="evidence">{evidence}</TabsContent>
        </Tabs>
      )}

      <ChatComposer
        onSubmit={submit}
        onStop={stop}
        busy={streaming}
        disabled={kbId === ""}
        className="sticky bottom-0 mt-auto bg-canvas pt-2"
      />
    </div>
  );
}
