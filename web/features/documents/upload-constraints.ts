/**
 * 上传的前置约束——**必须与后端一致**，否则用户选了文件、传上去才被拒。
 *
 * - 后缀白名单：`src/pyknora/services/ingest/parser.py:19 SUPPORTED_SUFFIXES`
 * - 大小上限：`src/pyknora/core/settings.py:101 max_upload_bytes = 50 * 1024 * 1024`
 *
 * 这里的检查是**提前反馈**，不是安全边界：真正的校验在后端（`documents.py:175-184`
 * 校验后缀、`_read_bounded` 校验大小）。前端放过了后端仍会拒，反过来前端拒了后端未必拒
 * ——所以文案只说"当前支持"，不说"绝对不允许"。
 */

import { formatBytes } from "@/lib/utils";

/** 50 MiB，与 `settings.max_upload_bytes` 逐字节一致（不是 50 MB）。 */
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

/** 后端白名单，顺序照抄 `parser.py`，方便比对。 */
export const SUPPORTED_SUFFIXES = [".pdf", ".docx", ".txt", ".md", ".markdown", ".html", ".htm"] as const;

/** `<input type="file" accept>` 的值。 */
export const ACCEPT_ATTRIBUTE = SUPPORTED_SUFFIXES.join(",");

/** 展示用的可读清单（界面文案，不参与校验）。 */
export const SUPPORTED_SUFFIXES_LABEL = "PDF、DOCX、TXT、Markdown（.md/.markdown）、HTML（.html/.htm）";

/** 取小写后缀；无点或点在末尾时返回空串（与后端 `_suffix_of` 同口径）。 */
export function suffixOf(filename: string): string {
  const index = filename.lastIndexOf(".");
  return index >= 0 ? filename.slice(index).toLowerCase() : "";
}

export type UploadRejectionReason = "unsupported-suffix" | "too-large";

export interface UploadRejection {
  reason: UploadRejectionReason;
  /** 可直接展示给用户的一句话。 */
  message: string;
}

/**
 * 校验一个文件是否值得发出去。通过返回 `null`。
 *
 * 用 `File.size`（字节）而不是读内容：50MiB 的边界要按字节判，`file.size` 就是字节数。
 */
export function validateUploadFile(file: File): UploadRejection | null {
  const suffix = suffixOf(file.name);
  if (!SUPPORTED_SUFFIXES.some((supported) => supported === suffix)) {
    return {
      reason: "unsupported-suffix",
      message: suffix
        ? `不支持的文件类型 ${suffix}；当前支持：${SUPPORTED_SUFFIXES_LABEL}`
        : `文件名没有后缀，无法判断类型；当前支持：${SUPPORTED_SUFFIXES_LABEL}`,
    };
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return {
      reason: "too-large",
      message: `文件 ${formatBytes(file.size)} 超过上限 ${formatBytes(MAX_UPLOAD_BYTES)}`,
    };
  }
  return null;
}
