import {
  ApiError,
  INVALID_RESPONSE_CODE,
  NETWORK_ERROR_CODE,
  NETWORK_ERROR_STATUS,
} from "@/lib/api/client";

/**
 * 认证错误 → 中文文案。
 *
 * 三条纪律：
 * 1. **不区分"用户不存在"与"密码错误"**：后端登录失败只回一种 401（防用户枚举），
 *    前端文案也必须只有一种，否则把服务端刻意关掉的口子又打开了。
 * 2. **不泄露账号是否存在**：409 只说"用户名或邮箱已被注册"，与后端
 *    `AppError(CONFLICT, "account already exists")` 口径一致（后端同样不区分撞的是哪个）。
 * 3. **500 带上请求标识**：用户报障时截图里得有 request_id，否则服务端日志对不上。
 */

export type AuthField = "username" | "password" | "email" | "form";

/** 本地校验失败（还没发请求就能判定）。带上字段名，页面才能把提示挂到对应输入框。 */
export class AuthInputError extends Error {
  readonly field: AuthField;

  constructor(field: AuthField, message: string) {
    super(message);
    this.name = "AuthInputError";
    this.field = field;
  }
}

/** 把 `request_id` 渲染进文案的小工具；没有就不加括号尾巴。 */
function withRequestId(base: string, requestId: string | null | undefined): string {
  return requestId ? `${base}（请求标识 ${requestId}）` : base;
}

/**
 * @param context 决定 401 的措辞：登录失败是"用户名或密码错误"，
 *                已登录状态下 401 是"登录状态已失效"。
 */
export function describeAuthError(error: unknown, context: "login" | "register" | "session" = "login"): string {
  if (error instanceof AuthInputError) return error.message;

  if (error instanceof ApiError) {
    // 网络层失败被客户端包成 `status 0 / NETWORK_ERROR`：这不是"服务端拒绝"，
    // 文案必须指向网络，否则用户会以为账号被锁。
    if (error.code === NETWORK_ERROR_CODE || error.status === NETWORK_ERROR_STATUS) {
      return "无法连接服务，请检查网络后重试";
    }
    if (error.code === INVALID_RESPONSE_CODE) {
      return withRequestId("服务返回了无法识别的响应，请稍后重试", error.requestId);
    }
    switch (error.code) {
      case "UNAUTHENTICATED":
        return context === "login" ? "用户名或密码错误" : "登录状态已失效，请重新登录";
      case "CONFLICT":
        return "该用户名或邮箱已被注册";
      case "FORBIDDEN":
        return "账号正确，但尚未加入任何工作区，请联系管理员开通";
      case "INVALID_REQUEST":
        return "输入不符合要求：用户名 3–64 位字母/数字/下划线/点/短横线，密码至少 8 位";
      case "DEPENDENCY_UNAVAILABLE":
        return "服务暂不可用，请稍后重试";
      case "INTERNAL_ERROR":
        return withRequestId("服务端错误，请稍后重试", error.requestId);
      default:
        break;
    }
    if (error.status === 401) {
      return context === "login" ? "用户名或密码错误" : "登录状态已失效，请重新登录";
    }
    if (error.status === 409) return "该用户名或邮箱已被注册";
    if (error.status >= 500) {
      return withRequestId(`服务端错误（${error.status}），请稍后重试`, error.requestId);
    }
    return withRequestId(`请求失败（${error.status}）`, error.requestId);
  }

  // fetch 在网络层失败时抛的就是 TypeError；与"服务端明确拒绝"必须区分开。
  if (error instanceof TypeError) return "无法连接服务，请检查网络后重试";

  if (error instanceof Error && error.message.length > 0) return error.message;

  return "操作失败，请稍后重试";
}
