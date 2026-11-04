// 图标要从 `"use client"` 模块取：phosphor 在模块求值期调用 `createContext`，
// 直接 import 会让本模块在 Server 侧被求值，整页 SSR 崩在 createContext 上。
// 详见 `components/app-shell/nav-icons.tsx` 的注释。
import { BooksIcon, CodeIcon, UsersIcon, type Icon } from "@/components/app-shell/nav-icons";

/**
 * 侧栏与窄屏抽屉共用的导航模型：加/删一项只改这里，两个入口自动同步，
 * 不会出现"桌面能点、手机看不见"的口径分裂。
 */
export type NavItem = {
  readonly href: string;
  readonly label: string;
  readonly hint: string;
  readonly icon: Icon;
  /** 外链（新标签页打开）。外链永不参与 `aria-current` 判定。 */
  readonly external?: boolean;
};

export const NAV_ITEMS: readonly NavItem[] = [
  {
    href: "/kbs",
    label: "知识库",
    hint: "知识库、文档与检索入口",
    icon: BooksIcon,
  },
  {
    href: "/settings/members",
    label: "成员",
    hint: "工作区成员与角色",
    icon: UsersIcon,
  },
  {
    href: "/api-docs",
    label: "接口文档",
    hint: "中文接口说明书：端点、错误码、权限点",
    icon: CodeIcon,
  },
];

/**
 * 当前路由是否属于该导航项。
 *
 * 用前缀匹配而不是全等：`/kbs` 的高亮必须覆盖 `/kbs/<id>/documents`、`/kbs/<id>/chat`
 * 这类子路由，否则一进知识库内部侧栏就全暗了。外链一律 `false`——它是 `/openapi.json`
 * 的代理地址，落不到应用路由里，点亮它只会误导用户以为"正在看接口文档页"。
 */
export function isNavItemActive(item: NavItem, pathname: string): boolean {
  if (item.external === true) return false;
  if (pathname === item.href) return true;
  return pathname.startsWith(`${item.href}/`);
}
