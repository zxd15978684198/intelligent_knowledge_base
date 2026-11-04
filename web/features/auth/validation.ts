/**
 * 客户端表单校验——**与后端 `src/pyknora/api/v1/auth.py` 的约束逐条对齐**。
 *
 * 为什么前端也要校验：不是为了安全（安全由后端 422/401 负责），而是为了在用户
 * 离开输入框时立刻给出中文原因，而不是等一次往返后拿回 `INVALID_REQUEST`。
 * 两边规则必须一致，否则会出现"前端说可以、后端 422"的死角。
 *
 * 后端 `RegisterRequest` 的真实约束：
 * - `username`：`min_length=3, max_length=64, pattern=^[A-Za-z0-9_.-]+$`
 * - `email`：`min_length=3, max_length=255`，必须含 `@` 且不在首尾（**必填**）
 * - `password`：`min_length=8, max_length=128`，且 ≤72 字节（bcrypt 上限）
 *
 * `LoginRequest` 只要求非空（老账号可能早于当前口令策略）。
 */

export const USERNAME_MIN_LENGTH = 3;
export const USERNAME_MAX_LENGTH = 64;
export const USERNAME_PATTERN = /^[A-Za-z0-9_.-]+$/;

export const PASSWORD_MIN_LENGTH = 8;
export const PASSWORD_MAX_LENGTH = 128;
/** bcrypt 5.x 的硬上限；后端 `password_within_limit` 同口径。 */
export const PASSWORD_MAX_BYTES = 72;

export const EMAIL_MIN_LENGTH = 3;
export const EMAIL_MAX_LENGTH = 255;

export interface FieldErrors {
  username?: string;
  password?: string;
  email?: string;
}

export interface LoginValues {
  username: string;
  password: string;
}

export interface RegisterValues extends LoginValues {
  email: string;
}

/** 口令字节数（UTF-8）。中文一个字 3 字节，所以"8 个汉字"是 24 字节而不是 8。 */
export function passwordByteLength(value: string): number {
  return new TextEncoder().encode(value).length;
}

export function validateUsernameForLogin(value: string): string | undefined {
  const trimmed = value.trim();
  if (trimmed.length === 0) return "请输入用户名";
  if (trimmed.length > USERNAME_MAX_LENGTH) return `用户名最长 ${USERNAME_MAX_LENGTH} 个字符`;
  return undefined;
}

export function validateUsernameForRegister(value: string): string | undefined {
  const trimmed = value.trim();
  if (trimmed.length === 0) return "请输入用户名";
  if (trimmed.length < USERNAME_MIN_LENGTH) return `用户名至少 ${USERNAME_MIN_LENGTH} 个字符`;
  if (trimmed.length > USERNAME_MAX_LENGTH) return `用户名最长 ${USERNAME_MAX_LENGTH} 个字符`;
  if (!USERNAME_PATTERN.test(trimmed)) {
    return "用户名只能包含字母、数字、下划线、点和短横线";
  }
  return undefined;
}

export function validatePasswordForLogin(value: string): string | undefined {
  if (value.length === 0) return "请输入密码";
  if (value.length > PASSWORD_MAX_LENGTH) return `密码最长 ${PASSWORD_MAX_LENGTH} 个字符`;
  return undefined;
}

export function validatePasswordForRegister(value: string): string | undefined {
  if (value.length === 0) return "请输入密码";
  if (value.length < PASSWORD_MIN_LENGTH) return `密码至少 ${PASSWORD_MIN_LENGTH} 位`;
  if (value.length > PASSWORD_MAX_LENGTH) return `密码最长 ${PASSWORD_MAX_LENGTH} 个字符`;
  if (passwordByteLength(value) > PASSWORD_MAX_BYTES) {
    return `密码最长 ${PASSWORD_MAX_BYTES} 字节（中文约 24 个字）`;
  }
  return undefined;
}

export function validateEmailForRegister(value: string): string | undefined {
  const trimmed = value.trim();
  if (trimmed.length === 0) return "请输入邮箱";
  if (trimmed.length < EMAIL_MIN_LENGTH || trimmed.length > EMAIL_MAX_LENGTH) {
    return `邮箱长度需在 ${EMAIL_MIN_LENGTH}–${EMAIL_MAX_LENGTH} 个字符之间`;
  }
  // 与后端同口径：不做完整 RFC 校验（本仓库不发验证邮件），只挡明显不是邮箱的输入。
  if (!trimmed.includes("@") || trimmed.startsWith("@") || trimmed.endsWith("@")) {
    return "请输入有效的邮箱地址";
  }
  return undefined;
}

/** 收集非空错误；返回空对象表示通过。 */
export function validateLogin(values: LoginValues): FieldErrors {
  const errors: FieldErrors = {};
  const username = validateUsernameForLogin(values.username);
  if (username !== undefined) errors.username = username;
  const password = validatePasswordForLogin(values.password);
  if (password !== undefined) errors.password = password;
  return errors;
}

export function validateRegister(values: RegisterValues): FieldErrors {
  const errors: FieldErrors = {};
  const username = validateUsernameForRegister(values.username);
  if (username !== undefined) errors.username = username;
  const email = validateEmailForRegister(values.email);
  if (email !== undefined) errors.email = email;
  const password = validatePasswordForRegister(values.password);
  if (password !== undefined) errors.password = password;
  return errors;
}

/** 是否有错误（`Object.keys` 在 strict 下比 `Object.values().some` 更直观）。 */
export function hasFieldErrors(errors: FieldErrors): boolean {
  return Object.keys(errors).length > 0;
}

/** 取第一个错误（顺序固定 username → email → password），用于抛 `AuthInputError`。 */
export function firstFieldError(
  errors: FieldErrors,
): { field: keyof FieldErrors; message: string } | null {
  const order: readonly (keyof FieldErrors)[] = ["username", "email", "password"];
  for (const field of order) {
    const message = errors[field];
    if (message !== undefined) return { field, message };
  }
  return null;
}
