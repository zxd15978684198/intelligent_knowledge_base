import { useCallback, useMemo, useState } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { qk } from "@/lib/api/hooks";
import {
  DEFAULT_KB_PAGE_SIZE,
  createKb,
  deleteKb,
  getKb,
  listKbs,
  renameKb,
  type CreateKbInput,
  type DeleteKbInput,
  type KbPage,
  type KbSummary,
} from "./api";
import { describeKbError, isCursorExpired, isRevisionConflict } from "./errors";

/**
 * 知识库的服务端数据层（计划 D5：TanStack Query 管服务端数据）。
 *
 * 所有查询键都以 `qk.kbs(workspaceId)` 开头——工作区必须进键，
 * 否则切换空间会命中别人的缓存；前缀相同也让"精确失效"只落在 KB 这一片。
 */

/** 工作区未选定用 `null` 表示，与后端"回落 JWT 默认空间"的行为对应。 */
export type KbWorkspaceId = string | null;

/**
 * `qk.kbs(ws)` 要求真实的 workspaceId，而 P0 只有一个默认空间（切换器还没做）。
 * 未选定工作区时用这个稳定占位拼键：既不会造出"每次渲染一个新 key"，
 * 也保证将来接上工作区切换器时，换成真实 ws 只是换一片缓存，不会读到旧空间的列表。
 */
export const DEFAULT_WORKSPACE_KEY = "default";

export interface KbScope {
  workspaceId?: KbWorkspaceId;
}

export interface UseKbsOptions extends KbScope {
  limit?: number;
  enabled?: boolean;
}

function kbsKey(workspaceId: KbWorkspaceId) {
  return qk.kbs(workspaceId ?? DEFAULT_WORKSPACE_KEY);
}

/** 详情键挂在列表键之下：失效列表时详情一起刷新。 */
function kbDetailKey(workspaceId: KbWorkspaceId, kbId: string) {
  return [...kbsKey(workspaceId), "detail", kbId] as const;
}

/** 游标分页列表：`next_cursor` 为 null 即到底，页内条数不是总数。 */
export function useKbs(options: UseKbsOptions = {}) {
  const limit = options.limit ?? DEFAULT_KB_PAGE_SIZE;
  const workspaceId = options.workspaceId ?? null;
  return useInfiniteQuery({
    queryKey: [...kbsKey(workspaceId), { limit }],
    queryFn: ({ pageParam, signal }) =>
      listKbs({ cursor: pageParam, limit, workspaceId, signal }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    enabled: options.enabled ?? true,
  });
}

/** 单个知识库详情（二级壳的标题用）。 */
export function useKb(kbId: string, options: KbScope & { enabled?: boolean } = {}) {
  const workspaceId = options.workspaceId ?? null;
  return useQuery({
    queryKey: kbDetailKey(workspaceId, kbId),
    queryFn: ({ signal }) => getKb(kbId, { workspaceId, signal }),
    enabled: (options.enabled ?? true) && kbId.length > 0,
  });
}

/** 新建后失效当前工作区的 KB 缓存（列表与已加载的所有页一起刷新）。 */
export function useCreateKb(workspaceId: KbWorkspaceId = null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateKbInput) => createKb(input, { workspaceId }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: kbsKey(workspaceId) });
    },
  });
}

/**
 * 改名。409 说明别人先改了：先重新读取最新版本，再让调用方提示重试。
 * 这里 `await` 失效，保证"重新读取"发生在错误回到界面之前。
 */
export function useRenameKb(workspaceId: KbWorkspaceId = null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: Omit<Parameters<typeof renameKb>[0], "workspaceId">) =>
      renameKb({ ...input, workspaceId }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: kbsKey(workspaceId) });
    },
    onError: async (error) => {
      if (isRevisionConflict(error)) {
        await queryClient.invalidateQueries({ queryKey: kbsKey(workspaceId) });
      }
    },
  });
}

/** 删除（202 受理）。同样带 `If-Match`，冲突处理与改名一致。 */
export function useDeleteKb(workspaceId: KbWorkspaceId = null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: Omit<DeleteKbInput, "workspaceId">) => deleteKb({ ...input, workspaceId }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: kbsKey(workspaceId) });
    },
    onError: async (error) => {
      if (isRevisionConflict(error)) {
        await queryClient.invalidateQueries({ queryKey: kbsKey(workspaceId) });
      }
    },
  });
}

export interface KbListController {
  items: KbSummary[];
  pages: KbPage[];
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  isFetching: boolean;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  /** 410 重置提示等一次性文案；消费后调用 `clearNotice`。 */
  notice: string | null;
  loadMore: () => Promise<void>;
  reload: () => Promise<void>;
  clearNotice: () => void;
}

/**
 * 列表页的控制器：把"加载更多 + 410 重置 + 文案"收在一处，
 * 页面只负责渲染，单测也能脱离 UI 直接驱动（见 tests/unit/features/kbs）。
 */
export function useKbListController(options: UseKbsOptions = {}): KbListController {
  const workspaceId = options.workspaceId ?? null;
  const queryClient = useQueryClient();
  const query = useKbs(options);
  const [notice, setNotice] = useState<string | null>(null);

  const pages = useMemo(() => query.data?.pages ?? [], [query.data]);

  const items = useMemo(() => pages.flatMap((page) => page.items), [pages]);

  const loadMore = useCallback(async () => {
    if (!query.hasNextPage || query.isFetchingNextPage) return;
    setNotice(null);
    const result = await query.fetchNextPage();
    if (!result.isFetchNextPageError) return;
    const error: unknown = result.error;
    if (isCursorExpired(error)) {
      // 410：游标签名过期（换密钥/过期）。清空所有页，回到第一页再来一遍。
      await queryClient.resetQueries({ queryKey: kbsKey(workspaceId) });
      setNotice("分页游标已过期，已重新加载第一页。");
      return;
    }
    setNotice(describeKbError(error, "load"));
  }, [
    query,
    queryClient,
    workspaceId,
  ]);

  const reload = useCallback(async () => {
    setNotice(null);
    await query.refetch();
  }, [query]);

  const clearNotice = useCallback(() => setNotice(null), []);

  return {
    items,
    pages,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    isFetching: query.isFetching,
    hasNextPage: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
    notice,
    loadMore,
    reload,
    clearNotice,
  };
}
