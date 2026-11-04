import { describe, expect, it } from "vitest";

import { DEFAULT_CHUNK_PAGE, DEFAULT_DOCUMENT_PAGE, qk, type PageParams } from "@/lib/api/hooks";

/**
 * query key 工厂的契约。
 *
 * 这里测的不是"函数返回值形状"，而是三条会让缓存**静默串数据**的规则：
 * 1. `workspaceId` 必须在每一把 key 里（切换工作区后旧缓存必须失效）；
 * 2. 分页参数进 key（否则第二页会命中第一页的缓存）；
 * 3. 前缀失效可用（`invalidateQueries` 靠它）。
 */

const WORKSPACE = "ws-a";
const OTHER_WORKSPACE = "ws-b";
const KB = "kb-1";
const DOC = "doc-1";

const PAGE: PageParams = { limit: 50, offset: 0 };

describe("qk：workspaceId 必在 key 里", () => {
  // [名字, 当前工作区的 key, 换一个工作区的 key]
  const keys: Array<[string, readonly unknown[], readonly unknown[]]> = [
    ["kbs", qk.kbs(WORKSPACE), qk.kbs(OTHER_WORKSPACE)],
    ["documents", qk.documents(WORKSPACE, KB, PAGE), qk.documents(OTHER_WORKSPACE, KB, PAGE)],
    ["document", qk.document(WORKSPACE, DOC), qk.document(OTHER_WORKSPACE, DOC)],
    ["chunks", qk.chunks(WORKSPACE, DOC, PAGE), qk.chunks(OTHER_WORKSPACE, DOC, PAGE)],
    ["jobs", qk.jobs(WORKSPACE, "job-1"), qk.jobs(OTHER_WORKSPACE, "job-1")],
    ["members", qk.members(WORKSPACE), qk.members(OTHER_WORKSPACE)],
  ];

  it.each(keys)("%s 的第二段就是 workspaceId", (_name, key) => {
    expect(key[0]).toBeDefined();
    expect(key[1]).toBe(WORKSPACE);
  });

  it.each(keys)("%s 换工作区后 key 必定不同", (_name, key, other) => {
    expect(other).not.toEqual(key);
  });
});

describe("qk.documents：分页与知识库进 key", () => {
  it("同一个知识库、不同页 → 不同 key", () => {
    expect(qk.documents(WORKSPACE, KB, { limit: 50, offset: 0 })).not.toEqual(
      qk.documents(WORKSPACE, KB, { limit: 50, offset: 50 }),
    );
  });

  it("不同知识库 → 不同 key", () => {
    expect(qk.documents(WORKSPACE, KB, PAGE)).not.toEqual(qk.documents(WORKSPACE, "kb-2", PAGE));
  });

  it("相同输入深相等（TanStack 靠结构比较命中缓存）", () => {
    expect(qk.documents(WORKSPACE, KB, { limit: 50, offset: 0 })).toEqual(
      qk.documents(WORKSPACE, KB, { limit: 50, offset: 0 }),
    );
  });

  it("调用方塞进来的多余字段不会进 key（否则同样的查询会分裂成多份缓存）", () => {
    const dirty = { limit: 20, offset: 0, cursor: "should-not-appear" } as PageParams;
    expect(qk.documents(WORKSPACE, KB, dirty)).toEqual(["documents", WORKSPACE, KB, { limit: 20, offset: 0 }]);
  });
});

describe("qk.chunks", () => {
  it("分页参数进 key", () => {
    expect(qk.chunks(WORKSPACE, DOC, { limit: 100, offset: 0 })).not.toEqual(
      qk.chunks(WORKSPACE, DOC, { limit: 100, offset: 100 }),
    );
  });

  it("与 documents 不混淆（切片挂在文档下，不是知识库下）", () => {
    expect(qk.chunks(WORKSPACE, DOC, PAGE)[0]).toBe("chunks");
    expect(qk.documents(WORKSPACE, DOC, PAGE)[0]).toBe("documents");
  });
});

describe("qk：前缀失效可用", () => {
  it("documents 的 key 前缀是 [\"documents\", ws]", () => {
    // TanStack 的 invalidateQueries 是前缀匹配：这两段能命中该工作区的全部文档列表。
    expect(qk.documents(WORKSPACE, KB, PAGE).slice(0, 2)).toEqual(["documents", WORKSPACE]);
  });

  it("chunks / jobs 同理", () => {
    expect(qk.chunks(WORKSPACE, DOC, PAGE).slice(0, 2)).toEqual(["chunks", WORKSPACE]);
    expect(qk.jobs(WORKSPACE, "job-1").slice(0, 2)).toEqual(["jobs", WORKSPACE]);
  });
});

describe("qk：资源之间不会互相命中", () => {
  it("六种 key 两两不同（document 是单数，避免详情命中列表缓存）", () => {
    const all = [
      qk.kbs(WORKSPACE),
      qk.documents(WORKSPACE, DOC, PAGE),
      qk.document(WORKSPACE, DOC),
      qk.chunks(WORKSPACE, DOC, PAGE),
      qk.jobs(WORKSPACE, DOC),
      qk.members(WORKSPACE),
    ].map((key) => JSON.stringify(key));

    expect(new Set(all).size).toBe(all.length);
  });

  it("jobs 的 jobId 进 key", () => {
    expect(qk.jobs(WORKSPACE, "job-1")).not.toEqual(qk.jobs(WORKSPACE, "job-2"));
  });
});

describe("默认页大小与后端默认值一致", () => {
  it("文档默认 50（api/v1/documents.py 的 limit 默认值）", () => {
    expect(DEFAULT_DOCUMENT_PAGE).toEqual({ limit: 50, offset: 0 });
  });

  it("切片默认 100", () => {
    expect(DEFAULT_CHUNK_PAGE).toEqual({ limit: 100, offset: 0 });
  });
});
