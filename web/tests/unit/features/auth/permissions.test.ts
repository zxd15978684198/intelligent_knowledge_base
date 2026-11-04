import { describe, expect, it } from "vitest";

import "./browser-storage";

import {
  ALL_PERMISSIONS,
  PERMISSIONS,
  describePermission,
  hasAnyPermission,
  hasPermission,
  missingPermissions,
  normalizePermissions,
} from "@/lib/auth/permissions";

/**
 * 权限点目录与判权。
 *
 * 最值钱的一条是"与后端逐字一致"：拼错一个字符不会报错，只会静默 403。
 * 所以这里用**字面量清单**（而不是引用 `PERMISSIONS`）做集合相等断言。
 */
const BACKEND_PERMISSIONS = [
  "knowledge_base.read",
  "knowledge_base.write",
  "knowledge_base.delete",
  "document.read",
  "document.write",
  "document.delete",
  "chunk.read",
  "job.read",
  "job.cancel",
  "query.run",
  "chat.run",
  "lineage.read",
  "index.rebuild",
  "workspace.manage_members",
  "workspace.manage_roles",
  "platform.manage_roles",
] as const;

describe("lib/auth/permissions", () => {
  it("权限点与后端 permissions.py 逐字一致（16 条，无遗漏无多余）", () => {
    expect([...ALL_PERMISSIONS].sort()).toEqual([...BACKEND_PERMISSIONS].sort());
    expect(new Set(ALL_PERMISSIONS).size).toBe(BACKEND_PERMISSIONS.length);
  });

  it("hasPermission 精确匹配，不做前缀/通配推导", () => {
    const granted = ["document.write"];
    expect(hasPermission(granted, "document.write")).toBe(true);
    // 没有继承：有 write 不代表有 read（后端 role_grants 逐条授予）
    expect(hasPermission(granted, "document.read")).toBe(false);
    // 不做前缀匹配
    expect(hasPermission(["document"], "document.write")).toBe(false);
    expect(hasPermission(["document.write.all"], "document.write")).toBe(false);
    expect(hasPermission([], "document.write")).toBe(false);
    expect(hasPermission(granted, "")).toBe(false);
  });

  it("hasAnyPermission / missingPermissions 覆盖多权限场景", () => {
    const granted = [PERMISSIONS.documentRead];
    expect(hasAnyPermission(granted, [PERMISSIONS.documentWrite, PERMISSIONS.documentRead])).toBe(true);
    expect(hasAnyPermission(granted, [PERMISSIONS.jobCancel])).toBe(false);
    expect(
      missingPermissions(granted, [PERMISSIONS.documentRead, PERMISSIONS.documentWrite]),
    ).toEqual(["document.write"]);
    expect(missingPermissions(granted, [])).toEqual([]);
  });

  it("describePermission 给中文说明，未知权限点原样返回（便于发现拼写错误）", () => {
    expect(describePermission(PERMISSIONS.documentWrite)).toBe("上传或修改文档");
    expect(describePermission(PERMISSIONS.knowledgeBaseDelete)).toBe("删除知识库");
    expect(describePermission("document.writ")).toBe("document.writ");
  });

  it("normalizePermissions 丢弃非字符串元素，非数组返回 null", () => {
    expect(normalizePermissions(["a", 1, null, "b"])).toEqual(["a", "b"]);
    expect(normalizePermissions([])).toEqual([]);
    expect(normalizePermissions(undefined)).toBeNull();
    expect(normalizePermissions("document.write")).toBeNull();
    expect(normalizePermissions({ 0: "a" })).toBeNull();
  });
});
