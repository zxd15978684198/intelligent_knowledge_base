"use client";

import { useId, useState } from "react";

import { shellEnter } from "@/components/app-shell/shell-motion";
import { Button, Input, Label } from "@/components/ui";
import { cn } from "@/lib/utils";

import { useAuthForm, type AuthMode } from "../use-auth-form";
import { PASSWORD_MAX_BYTES, PASSWORD_MIN_LENGTH } from "../validation";

export interface AuthFormProps {
  mode: AuthMode;
}

const COPY: Record<AuthMode, { submit: string; pending: string }> = {
  login: { submit: "登录", pending: "登录中…" },
  register: { submit: "注册并登录", pending: "注册中…" },
};

const PASSWORD_HINT = `至少 ${PASSWORD_MIN_LENGTH} 位；服务端按 UTF-8 计 ${PASSWORD_MAX_BYTES} 字节上限（中文约 24 个字）。`;

/**
 * 登录 / 注册表单本体。
 *
 * 可访问性要点（评审会逐条看）：
 * - 每个输入框都有 `<label htmlFor>` 关联，id 由 `useId()` 生成（同页两个 Tab 各一份，不会撞 id）；
 * - 字段错误用 `role="alert"` + `aria-describedby` 关联到输入框，输入框同时标 `aria-invalid`；
 * - 表单级错误（服务端拒绝）也只用一个 `role="alert"` 区域，避免读屏重复播报；
 * - `noValidate` 关掉浏览器原生气泡，校验文案统一由本仓库给（中文化、可单测）；
 * - 提交中 `disabled` 同时作用于三个输入框和按钮，防止重复提交（后端不是幂等接口）。
 */
export function AuthForm({ mode }: AuthFormProps) {
  const formId = useId();
  const { values, errors, formError, pending, setField, submit } = useAuthForm(mode);
  const [showPassword, setShowPassword] = useState(false);

  const usernameId = `${formId}-username`;
  const usernameErrorId = `${usernameId}-error`;
  const emailId = `${formId}-email`;
  const emailErrorId = `${emailId}-error`;
  const passwordId = `${formId}-password`;
  const passwordErrorId = `${passwordId}-error`;
  const passwordHintId = `${passwordId}-hint`;

  const isRegister = mode === "register";
  const copy = COPY[mode];

  // I3 stagger：卡片本体已由 (auth)/layout.tsx 用 shellEnter(0) 入场，
  // 表单内部块从 step 1 起按 30ms 错落衔接；错误区 role="alert" 不加动画，保证即时可见/播报。
  const usernameEnter = shellEnter(1);
  const emailEnter = shellEnter(2);
  const passwordEnter = shellEnter(isRegister ? 3 : 2);
  const submitEnter = shellEnter(isRegister ? 4 : 3);

  return (
    <form
      className="space-y-5"
      onSubmit={submit}
      noValidate
      aria-label={isRegister ? "注册表单" : "登录表单"}
    >
      <div className={cn("space-y-2", usernameEnter.className)} style={usernameEnter.style}>
        <Label htmlFor={usernameId} required>
          用户名
        </Label>
        <Input
          id={usernameId}
          name="username"
          type="text"
          value={values.username}
          onChange={(event) => setField("username", event.target.value)}
          disabled={pending}
          required
          autoComplete="username"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          placeholder={isRegister ? "3–64 位字母、数字、下划线、点或短横线" : "请输入用户名"}
          invalid={errors.username !== undefined}
          aria-describedby={errors.username === undefined ? undefined : usernameErrorId}
        />
        {errors.username === undefined ? null : (
          <p id={usernameErrorId} role="alert" className="text-xs leading-relaxed text-danger">
            {errors.username}
          </p>
        )}
      </div>

      {isRegister ? (
        <div className={cn("space-y-2", emailEnter.className)} style={emailEnter.style}>
          <Label htmlFor={emailId} required>
            邮箱
          </Label>
          <Input
            id={emailId}
            name="email"
            type="email"
            value={values.email}
            onChange={(event) => setField("email", event.target.value)}
            disabled={pending}
            required
            autoComplete="email"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            placeholder="you@example.com"
            invalid={errors.email !== undefined}
            aria-describedby={errors.email === undefined ? undefined : emailErrorId}
          />
          {errors.email === undefined ? null : (
            <p id={emailErrorId} role="alert" className="text-xs leading-relaxed text-danger">
              {errors.email}
            </p>
          )}
        </div>
      ) : null}

      <div className={cn("space-y-2", passwordEnter.className)} style={passwordEnter.style}>
        <div className="flex items-center justify-between gap-3">
          <Label htmlFor={passwordId} required>
            密码
          </Label>
          <button
            type="button"
            onClick={() => setShowPassword((visible) => !visible)}
            aria-pressed={showPassword}
            aria-controls={passwordId}
            className="rounded-sm text-xs font-medium text-primary underline-offset-2 transition-[color] duration-fast ease-out hover:text-primary-hover hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-ring"
          >
            {showPassword ? "隐藏密码" : "显示密码"}
          </button>
        </div>
        <Input
          id={passwordId}
          name="password"
          type={showPassword ? "text" : "password"}
          value={values.password}
          onChange={(event) => setField("password", event.target.value)}
          disabled={pending}
          required
          autoComplete={isRegister ? "new-password" : "current-password"}
          placeholder={isRegister ? `至少 ${PASSWORD_MIN_LENGTH} 位` : "请输入密码"}
          invalid={errors.password !== undefined}
          aria-describedby={
            errors.password === undefined
              ? isRegister
                ? passwordHintId
                : undefined
              : `${passwordErrorId} ${isRegister ? passwordHintId : ""}`.trim()
          }
        />
        {errors.password === undefined ? null : (
          <p id={passwordErrorId} role="alert" className="text-xs leading-relaxed text-danger">
            {errors.password}
          </p>
        )}
        {isRegister ? (
          <p id={passwordHintId} className="text-xs leading-relaxed text-fg-subtle">
            {PASSWORD_HINT}
          </p>
        ) : null}
      </div>

      {formError === null ? null : (
        <p
          role="alert"
          className="rounded-md border border-danger bg-danger-weak px-3 py-2 text-sm leading-relaxed text-danger"
        >
          {formError}
        </p>
      )}

      <div className={cn("space-y-3", submitEnter.className)} style={submitEnter.style}>
        <Button type="submit" loading={pending} className="w-full">
          {pending ? copy.pending : copy.submit}
        </Button>
        <p className="text-center text-xs leading-relaxed text-fg-subtle">
          {isRegister ? "注册成功后会自动登录。" : "登录成功后进入知识库列表。"}
        </p>
      </div>
    </form>
  );
}
