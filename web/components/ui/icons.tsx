import type { SVGProps } from "react";

/**
 * 内部图标（不进入 barrel）：只给 Dialog / Toast 的关闭按钮用。
 *
 * 基础组件不引图标库：任何使用 `<Button>` 的页面都不该因此把整套图标打进 bundle。
 * 业务页面自己的图标走 `@phosphor-icons/react`（计划 D7）。
 */
export function CloseIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 16 16"
      width={16}
      height={16}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      <path d="M4 4l8 8M12 4l-8 8" />
    </svg>
  );
}
