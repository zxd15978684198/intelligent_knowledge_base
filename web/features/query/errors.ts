import { isApiError } from "@/lib/api/client";

/**
 * 检索失败的判定与文案。
 *
 * 与计划 §5「异常处理」对齐，并且刻意保留后端的两条口径：
 * - 403 = 缺 `query.run` 权限（`api/v1/query.py:29` 的 `require_permission`）；
 * - 404 = 「不存在」与「无权访问」**同一个响应**。`require_kb_access` 不让客户端区分
 *   这两者，文案也就不能写成「你没有权限」——那等于把知识库是否存在泄漏出去。
 *
 * 5xx / 依赖不可用类错误把 `request_id` 一起给出来：后端日志按它检索，没有它反馈等于
 * 「随便猜」。
 */

export interface QueryErrorView {
  /** 一句主文案（标题位置）。 */
  title: string;
  /** 补充说明：为什么失败、下一步怎么做。 */
  detail: string;
  /** 是否值得重试：参数/权限/不存在类错误重试无意义，网络与 5xx 才值得。 */
  retryable: boolean;
}

function withRequestId(text: string, requestId: string | null): string {
  return requestId ? `${text}请求标识：${requestId}。` : text;
}

/**
 * 把任意异常翻译成可直接渲染的文案。
 *
 * 不认识的错误也给出 `HTTP <status> + code`：这一页是调试台，藏掉原始信息只会让
 * 排查多花一轮。
 */
export function describeQueryError(error: unknown): QueryErrorView {
  if (!isApiError(error)) {
    return {
      title: "检索请求未完成",
      detail: "网络异常或连接中断：请检查网络、代理与后端服务是否在运行，然后点击「重试本次检索」再试一次。",
      retryable: true,
    };
  }

  const requestId = error.requestId;

  if (error.status === 401 || error.code === "UNAUTHENTICATED") {
    return {
      title: "登录已过期（401）",
      detail: "登录凭据失效或缺失，请重新登录后再发起检索。",
      retryable: false,
    };
  }

  if (error.status === 403 || error.code === "FORBIDDEN") {
    return {
      title: "没有检索权限（403）",
      detail:
        "当前账号缺少 query.run 权限。检索调试会直接调用召回链路，属于受控操作：请让管理员授予该权限，或改用有权限的账号。",
      retryable: false,
    };
  }

  if (error.status === 404 || error.code === "RESOURCE_NOT_FOUND") {
    return {
      title: "知识库不存在或不属于当前工作区（404）",
      detail:
        "后端对「不存在」与「无权访问」统一返回 404，避免泄漏知识库是否存在。请确认地址里的知识库 ID 与当前工作区是否正确。",
      retryable: false,
    };
  }

  if (error.status === 503 || error.code === "DEPENDENCY_UNAVAILABLE") {
    return {
      title: "检索依赖不可用（503）",
      detail: withRequestId(
        "检索链路依赖未就绪（例如 embedding provider 未配置），系统跳过了本次检索。请联系管理员检查服务配置，然后点击「重试本次检索」。",
        requestId,
      ),
      retryable: true,
    };
  }

  if (error.status === 422 || error.code === "INVALID_REQUEST") {
    return {
      title: "请求不合法（422）",
      detail: withRequestId(error.message, requestId),
      retryable: false,
    };
  }

  if (error.status >= 500 || error.code === "INTERNAL_ERROR") {
    return {
      title: `服务异常（HTTP ${error.status}）`,
      detail: withRequestId("后端未能处理本次请求，请点击「重试本次检索」；若持续失败，请把请求标识一起反馈。", requestId),
      retryable: true,
    };
  }

  return {
    title: `检索失败（HTTP ${error.status}）`,
    detail: withRequestId(`${error.code}：${error.message}`, requestId),
    retryable: error.status >= 500,
  };
}
