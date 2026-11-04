/**
 * TanStack Query 的 query key 工厂（N2）。
 *
 * 三条硬规则，都是踩过才知道值钱的：
 *
 * 1. **每个 key 的第二段必须是 `workspaceId`**。同一个浏览器切换工作区后，缓存里的
 *    知识库/文档/成员全都失效，key 里不带 ws 就会出现"切了工作区但列表还是上一个的"
 *    ——而且它不会报错，只会安静地显示别人的数据。
 * 2. **分页参数进 key**（`{limit, offset}` 对象）。TanStack 用 `hashKey` 做结构比较，
 *    对象字面量的键顺序会影响匹配，所以这里**只有这一个工厂**能造分页 key，
 *    业务代码不许自己拼数组。
 * 3. **第二段位置固定**，于是 `queryClient.invalidateQueries({ queryKey: ["documents", ws] })`
 *    这种前缀失效可以直接用（TanStack 是前缀匹配）。
 *
 * 为什么只导出 key、不导出 `useQuery` 封装：每个列表/详情的 `staleTime`、`refetchInterval`
 * 都跟页面语义绑定（作业要轮询、成员不用），统一封一层只会逼着后来人绕开它。
 */

/** 分页参数。两种分页都复用这个形状，但语义不同，见各自工厂的注释。 */
export interface PageParams {
  limit: number;
  offset: number;
}

/** 文档列表的后端默认页大小（`api/v1/documents.py` 的 `limit` 默认 50，上限 500）。 */
export const DEFAULT_DOCUMENT_PAGE: PageParams = { limit: 50, offset: 0 };

/** 切片列表的后端默认页大小（默认 100）。 */
export const DEFAULT_CHUNK_PAGE: PageParams = { limit: 100, offset: 0 };

function toPageKey(page: PageParams): { limit: number; offset: number } {
  // 显式重建对象：调用方传进来的对象可能带别的字段，进缓存 key 会污染匹配。
  return { limit: page.limit, offset: page.offset };
}

export const qk = {
  /**
   * 知识库列表。**游标分页**（`next_cursor`），所以 key 里不带 offset——
   * 翻页是 `useInfiniteQuery` 用 `getNextPageParam` 拿 `next_cursor` 追加的，
   * 把 cursor 塞进 key 会让每一页变成独立查询、无法合并。
   */
  kbs: (ws: string): readonly unknown[] => ["kbs", ws],

  /**
   * 文档列表（`limit/offset` 分页，`total` 是全量，可算总页数）。
   * `kb` 进 key：不同知识库的文档页互不影响。
   */
  documents: (ws: string, kb: string, page: PageParams): readonly unknown[] => [
    "documents",
    ws,
    kb,
    toPageKey(page),
  ],

  /** 文档详情。与列表分开：列表里的 `DocumentSummary` 没有 duration/versions 之类的字段。 */
  document: (ws: string, doc: string): readonly unknown[] => ["document", ws, doc],

  /**
   * 切片列表（`limit/offset`）。
   * ⚠️ 响应的 `total` 是**页内条数**，不是全量（后端没 count），所以这里也无法预知总页数。
   */
  chunks: (ws: string, doc: string, page: PageParams): readonly unknown[] => ["chunks", ws, doc, toPageKey(page)],

  /** 单个作业。轮询用 `refetchInterval`，判停用 `isJobTerminal(job)`。 */
  jobs: (ws: string, jobId: string): readonly unknown[] => ["jobs", ws, jobId],

  /** 成员列表（不分页）。 */
  members: (ws: string): readonly unknown[] => ["members", ws],
} as const;
