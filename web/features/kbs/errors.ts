import { ApiError } from "@/lib/api/client";

/**
 * 知识库的异常判定与文案。
 *
 * 与计划 §5「异常处理」一一对应：
 * - 401 → 清理登录态（全局拦截器负责，这里只给文案）
 * - 403 → 缺少权限
 * - 404 → "不存在或无权限"（后端刻意的口径，别改写成"无权访问"）
 * - 409 `REVISION_CONFLICT` → 重新读取最新版本再提示，不静默覆盖
 * - 410 `CURSOR_EXPIRED` → 重新加载第一页
 * - 5xx → 展示 request_id 便于查日志
 */

export type KbErrorAction = "load" | "create" | "rename" | "delete";

/** 判别 `apiRequest` 抛出的错误；不是 ApiError（如网络错误）返回 false。 */
export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export function kbErrorCode(error: unknown): string | null {
  return isApiError(error) ? error.code : null;
}

export function isRevisionConflict(error: unknown): boolean {
  return isApiError(error) && error.code === "REVISION_CONFLICT";
}

export function isRevisionMissing(error: unknown): boolean {
  return isApiError(error) && error.code === "REVISION_MISSING";
}

export function isCursorExpired(error: unknown): boolean {
  return isApiError(error) && error.code === "CURSOR_EXPIRED";
}

export function isKbMissing(error: unknown): boolean {
  return isApiError(error) && (error.status === 404 || error.code === "RESOURCE_NOT_FOUND");
}

const ACTION_LABEL: Record<KbErrorAction, string> = {
  load: "加载知识库",
  create: "创建知识库",
  rename: "重命名知识库",
  delete: "删除知识库",
};

/** 请求标识附加在 5xx 文案尾部：没有它，用户报障时对不上后端日志。 */
function withRequestId(message: string, error: unknown): string {
  if (isApiError(error) && error.requestId) {
    return `${message}（请求标识：${error.requestId}）`;
  }
  return message;
}

/**
 * 把错误翻成一句可操作、用主动语态说明「我们没能…」的中文。未知错误不吞掉——
 * 兜底文案带状态码，避免出现"操作失败"这种查不出原因的空话。
 */
export function describeKbError(error: unknown, action: KbErrorAction = "load"): string {
  if (!isApiError(error)) {
    return `我们没能${ACTION_LABEL[action]}：网络异常，请检查连接后重试。`;
  }
  const code = error.code;
  if (code === "CURSOR_EXPIRED") {
    return "分页游标已过期，已重新加载第一页。";
  }
  if (code === "REVISION_CONFLICT") {
    return "该知识库已被其他人修改（版本冲突），已重新读取最新版本，请确认后重试。";
  }
  if (code === "REVISION_MISSING") {
    return "请求缺少版本号（If-Match），请刷新页面后重试。";
  }
  if (code === "RESOURCE_NOT_FOUND" || error.status === 404) {
    return "知识库不存在或无权限。";
  }
  if (code === "UNAUTHENTICATED" || error.status === 401) {
    return "登录状态已失效，请重新登录。";
  }
  if (code === "FORBIDDEN" || error.status === 403) {
    return `当前账号没有${ACTION_LABEL[action]}的权限。`;
  }
  if (code === "INVALID_REQUEST" || error.status === 422) {
    return `我们没能${ACTION_LABEL[action]}：提交内容不合法，请检查后重试。`;
  }
  if (code === "CONFLICT" || error.status === 409) {
    return `我们没能${ACTION_LABEL[action]}：操作与当前状态冲突，请刷新后重试。`;
  }
  if (code === "DEPENDENCY_UNAVAILABLE" || error.status === 503) {
    return withRequestId(`我们没能${ACTION_LABEL[action]}：依赖服务暂不可用，请稍后重试。`, error);
  }
  if (error.status >= 500) {
    return withRequestId(`我们没能${ACTION_LABEL[action]}：服务异常，请稍后重试。`, error);
  }
  return withRequestId(`我们没能${ACTION_LABEL[action]}（HTTP ${error.status}，${code}）。`, error);
}
