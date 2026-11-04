import { redirect } from "next/navigation";

/**
 * 站点根路径直接进知识库。
 *
 * P0 没有独立首页/仪表盘（计划 §1 明确排除营销页与虚拟指标卡），落地页就是唯一的工作
 * 入口，没必要先渲染一个空壳再让用户点一次。用服务端 `redirect`：不经过客户端 JS，
 * 首屏就能跳到目标路由。
 */
export default function RootPage() {
  redirect("/kbs");
}
