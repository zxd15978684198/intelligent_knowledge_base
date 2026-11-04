"use client";

import { useRouter } from "next/navigation";
import { useCallback, useState, type FormEvent } from "react";

import { useAuth } from "./auth-context";
import { AuthInputError, describeAuthError } from "./errors";
import { AFTER_LOGIN_PATH } from "./routes";
import {
  hasFieldErrors,
  validateLogin,
  validateRegister,
  type FieldErrors,
} from "./validation";

export type AuthMode = "login" | "register";

export interface AuthFormValues {
  username: string;
  email: string;
  password: string;
}

export interface AuthFormController {
  mode: AuthMode;
  values: AuthFormValues;
  /** 字段级错误：渲染在对应输入框下方（`role="alert"`），并驱动 `aria-invalid`。 */
  errors: FieldErrors;
  /** 表单级错误：服务端拒绝 / 网络失败。渲染在提交按钮上方（`role="alert"`）。 */
  formError: string | null;
  pending: boolean;
  setField: (field: keyof AuthFormValues, value: string) => void;
  submit: (event?: FormEvent<HTMLFormElement>) => Promise<void>;
}

const EMPTY_VALUES: AuthFormValues = { username: "", email: "", password: "" };

/**
 * 登录 / 注册表单的全部行为，抽成 hook 是为了两件事：
 *
 * 1. 页面（`app/(auth)/login/page.tsx`）只负责排版，行为可单测；
 * 2. 成功后的跳转只在这里发生（`router.replace(AFTER_LOGIN_PATH)`），
 *    e2e 与单测看到的是同一条路径，不会出现"页面里再跳一次"的分叉。
 *
 * `router.replace` 而不是 `push`：登录后按浏览器后退不该回到登录页
 * （回去也只会被 AuthGuard 再弹回来，多一次闪烁）。
 */
export function useAuthForm(mode: AuthMode): AuthFormController {
  const router = useRouter();
  // 只取稳定引用：整个 auth 对象每次 token/status 变化都会换新，作为依赖会让 submit 反复重建。
  const { login, register } = useAuth();

  const [values, setValues] = useState<AuthFormValues>(EMPTY_VALUES);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const setField = useCallback((field: keyof AuthFormValues, value: string): void => {
    setValues((prev) => ({ ...prev, [field]: value }));
    // 开始改这一格就撤掉它的报错，否则用户边改边看着旧提示。
    setErrors((prev) => {
      if (prev[field] === undefined) return prev;
      const next: FieldErrors = { ...prev };
      delete next[field];
      return next;
    });
    setFormError(null);
  }, []);

  const submit = useCallback(
    async (event?: FormEvent<HTMLFormElement>): Promise<void> => {
      event?.preventDefault();
      if (pending) return;

      const username = values.username.trim();
      const password = values.password;
      const email = values.email.trim();

      // 客户端先校验：能本地判定的错不要浪费一次往返（后端 422 只会回笼统的 INVALID_REQUEST）。
      const localErrors =
        mode === "login"
          ? validateLogin({ username, password })
          : validateRegister({ username, password, email });
      if (hasFieldErrors(localErrors)) {
        setErrors(localErrors);
        setFormError(null);
        return;
      }

      setErrors({});
      setFormError(null);
      setPending(true);
      try {
        if (mode === "login") {
          await login(username, password);
        } else {
          await register({ username, password, email });
        }
        router.replace(AFTER_LOGIN_PATH);
      } catch (error: unknown) {
        // 上下文层还会再校验一次（可能抛 AuthInputError）：落到对应字段，而不是只给一句笼统提示。
        if (error instanceof AuthInputError && error.field !== "form") {
          setErrors({ [error.field]: error.message });
        } else {
          setFormError(describeAuthError(error, mode));
        }
      } finally {
        setPending(false);
      }
    },
    [login, mode, pending, register, router, values.email, values.password, values.username],
  );

  return { mode, values, errors, formError, pending, setField, submit };
}
