"use client";

/**
 * 导航图标的客户端边界。
 *
 * `@phosphor-icons/react` 的 `context.es.js` 在**模块求值期**就调 `createContext()`
 * （IconContext），所以它只能在客户端模块里被 import。之前 `nav-items.ts` 直接引它，
 * 而这个模块会被 `components/app-shell/index.ts` 这个 **Server 模块**的 barrel 带进
 * RSC 图，结果是整个页面 SSR 抛
 * `TypeError: createContext only works in Client Components`，
 * 表现就是「注册点不动、显示密码不生效」——HTML 有、事件处理器一个没有。
 *
 * 把图标放在这个 `"use client"` 模块里，Server 侧只会拿到客户端引用，不会求值。
 */
export {
  ArrowSquareOutIcon,
  BooksIcon,
  CodeIcon,
  UsersIcon,
  type Icon,
} from "@phosphor-icons/react";
