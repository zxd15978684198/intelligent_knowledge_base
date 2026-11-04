"use client";

import { useId, useState, type FormEvent } from "react";
import {
  Button,
  Dialog,
  DialogContent,
  DialogTrigger,
  Field,
  Input,
  Select,
} from "@/components/ui";
import type { MemberAdd } from "@/lib/contracts";
import { describeMemberError } from "./errors";
import { useAddMember } from "./hooks";
import type { RolePermissionView } from "./api";
import {
  defaultMemberRole,
  describeRole,
  describeRolePermissions,
  selectableRoles,
} from "./roles";

/**
 * 后端的 `user_id` 是 UUID；先在客户端挡一次形状，省掉一次必然失败的 404/422。
 * 大小写不敏感：UUID 的十六进制部分两种写法都是合法的。
 */
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export interface MemberAddDialogProps {
  readonly workspaceId: string | null;
  readonly token: string | null;
  readonly roles: readonly RolePermissionView[] | null;
  readonly canManage: boolean;
}

/** 校验中间态：故意可变（先攒错、再一次性返回），对外仍是只读的 `ValidatedForm`。 */
interface FormErrors {
  principalId?: string;
  role?: string;
}

interface ValidatedForm {
  readonly errors: FormErrors;
  readonly body: MemberAdd | null;
}

/** 一次校验同时产出提示与请求体，避免"校验过了但请求体字段拼错"这类分叉。 */
function validateNewMember(input: {
  principalId: string;
  role: string;
  options: readonly RolePermissionView[];
}): ValidatedForm {
  const errors: FormErrors = {};
  const trimmed = input.principalId.trim();

  if (trimmed.length === 0) {
    errors.principalId = "请输入对方的 principal_id。";
  } else if (!UUID_PATTERN.test(trimmed)) {
    errors.principalId = "principal_id 必须是 UUID（8-4-4-4-12 位十六进制，含四个连字符）。";
  }

  if (input.options.length === 0) {
    errors.role = "角色目录还没读到，无法选择角色。";
  } else if (!input.options.some((option) => option.role_key === input.role)) {
    errors.role = "请选择一个该工作区真实存在的角色。";
  }

  if (errors.principalId !== undefined || errors.role !== undefined) {
    return { errors, body: null };
  }
  return { errors, body: { user_id: trimmed, role: input.role } };
}

/**
 * 添加成员对话框。
 *
 * 语义是"已有账号加入本工作区"，不是邀请制：没有邮件、没有邀请状态、没有待接受。
 * 也不显示人名——后端列表根本不返回用户名/邮箱，前端凭空去补只会造成两套口径。
 */
export function MemberAddDialog({
  workspaceId,
  token,
  roles,
  canManage,
}: MemberAddDialogProps) {
  const [open, setOpen] = useState(false);
  const [principalId, setPrincipalId] = useState("");
  const [role, setRole] = useState("");
  const [errors, setErrors] = useState<FormErrors>({});
  const [submitError, setSubmitError] = useState<string | null>(null);

  const idPrefix = useId();
  const addMember = useAddMember({ workspaceId, token });

  const options = selectableRoles(roles ?? []);
  // 目录是异步来的：用户没手动选时用默认角色，选过就尊重用户的选择。
  const effectiveRole = role.length > 0 ? role : defaultMemberRole(roles ?? []);
  const currentRole = options.find((option) => option.role_key === effectiveRole) ?? null;

  function handleOpenChange(next: boolean) {
    setOpen(next);
    if (next) {
      setPrincipalId("");
      setRole("");
      setErrors({});
      setSubmitError(null);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitError(null);
    const result = validateNewMember({
      principalId,
      role: effectiveRole,
      options,
    });
    setErrors(result.errors);
    if (result.body === null) return;

    try {
      await addMember.mutateAsync(result.body);
      setOpen(false);
    } catch (cause) {
      setSubmitError(describeMemberError(cause, "add"));
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button
          disabled={!canManage}
          title={canManage ? undefined : "需要「管理成员」权限（workspace.manage_members）"}
        >
          按 principal_id 添加成员
        </Button>
      </DialogTrigger>
      <DialogContent
        title="按 principal_id 添加成员"
        description="把已有账号直接加入本工作区：没有邮件、没有待接受状态，也不显示对方姓名或邮箱——后端只返回 user_id。"
        width="md"
      >
        <form
          className="flex flex-col gap-4"
          noValidate
          onSubmit={(event) => {
            void handleSubmit(event);
          }}
        >
          <Field
            id={`${idPrefix}-principal`}
            label="principal_id"
            required
            hint="该账号已有的 principal_id（UUID）。只有存在且处于 active 的账号能被加入。"
            error={errors.principalId}
          >
            <Input
              value={principalId}
              autoComplete="off"
              spellCheck={false}
              placeholder="6f1a2b3c-4d5e-4f60-8a7b-9c0d1e2f3a4b"
              onChange={(event) => {
                setPrincipalId(event.target.value);
                setErrors({});
              }}
            />
          </Field>

          <Field
            id={`${idPrefix}-role`}
            label="角色"
            required
            hint={
              currentRole
                ? describeRolePermissions(currentRole)
                : "角色来自本工作区的 roles 表；owner 只能通过「转移所有权」产生。"
            }
            error={errors.role}
          >
            <Select
              value={effectiveRole}
              disabled={options.length === 0}
              onChange={(event) => {
                setRole(event.target.value);
                setErrors({});
              }}
            >
              {options.map((option) => (
                <option key={option.role_key} value={option.role_key}>
                  {describeRole(option.role_key)}（{option.role_key}）
                </option>
              ))}
            </Select>
          </Field>

          {options.length === 0 ? (
            <p role="note" className="text-xs text-fg-muted">
              角色目录需要「管理成员」权限才能读取；没有它就只能看列表，不能加人。
            </p>
          ) : null}

          {submitError !== null ? (
            <p role="alert" className="text-sm text-danger">
              {submitError}
            </p>
          ) : null}

          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setOpen(false)}>
              取消
            </Button>
            <Button
              type="submit"
              loading={addMember.isPending}
              disabled={options.length === 0}
            >
              添加
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
