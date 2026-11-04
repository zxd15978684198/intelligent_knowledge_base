/**
 * 上传幂等键。
 *
 * 为什么前端要自己造键：`POST /knowledge-bases/{kb_id}/documents` 收 `Idempotency-Key`
 * 头（`documents.py:170`），同一次上传的**所有重试必须复用同一个键**——用户点"重传"
 * 时如果换新键，后端会当成新上传，同一份内容可能建出两个版本（除非撞上内容 hash
 * 去重）。所以键的生命周期是"一次上传意图"，不是"一次 HTTP 请求"：
 * 由 `UploadPanel` 在选中文件时生成，重试时原样复用，成功后丢弃。
 */

/**
 * 生成 UUID v4 字符串。
 *
 * 三级回落，全都不用 `any`：
 * 1. `crypto.randomUUID()`（安全上下文、Node 19+、现代浏览器）；
 * 2. `crypto.getRandomValues()` 手拼 v4（http 页面里 randomUUID 不可用）；
 * 3. 时间戳 + `Math.random()`（最后兜底；幂等性会弱一些，但请求仍能发出去）。
 */
export function newIdempotencyKey(): string {
  const cryptoObject: Crypto | undefined = typeof globalThis.crypto === "undefined" ? undefined : globalThis.crypto;

  if (cryptoObject !== undefined && typeof cryptoObject.randomUUID === "function") {
    return cryptoObject.randomUUID();
  }

  if (cryptoObject !== undefined && typeof cryptoObject.getRandomValues === "function") {
    const bytes = new Uint8Array(16);
    cryptoObject.getRandomValues(bytes);
    // RFC 4122 §4.4：第 6 字节高 4 位置 0100（版本 4），第 8 字节高 2 位置 10（variant）。
    bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
    bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
    const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
    return [
      hex.slice(0, 8),
      hex.slice(8, 12),
      hex.slice(12, 16),
      hex.slice(16, 20),
      hex.slice(20, 32),
    ].join("-");
  }

  return `idem-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}
