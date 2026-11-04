"use client";

import { useId } from "react";
import { Select } from "@/components/ui";
import type { RolePermissionView } from "./api";
import { describeRole, describeRolePermissions, selectableRoles } from "./roles";

export interface RoleSelectProps {
  readonly value: string;
  readonly roles: readonly RolePermissionView[];
  readonly onChange: (roleKey: string) => void;
  /** 无障碍名称；每行不同（例如带成员 id），避免同名下拉互相干扰。 */
  readonly label: string;
  readonly id?: string;
  readonly disabled?: boolean;
  readonly pending?: boolean;
  /** 是否在下面用一句话解释当前角色的权限点（来自 `GET .../roles`）。 */
  readonly showPermissions?: boolean;
}

/**
 * 角色下拉。
 *
 * 选项来自 `GET .../roles`（`role_key` + 权限点），**不含 owner**：后端不允许通过
 * 加成员/改角色产生 owner，只有「转移所有权」能。目录为空时下拉禁用并给出原因，
 * 而不是渲染一个空选择框让人反复点。
 */
export function RoleSelect({
  value,
  roles,
  onChange,
  label,
  id,
  disabled = false,
  pending = false,
  showPermissions = true,
}: RoleSelectProps) {
  const generatedId = useId();
  const selectId = id ?? generatedId;
  const options = selectableRoles(roles);
  const current = roles.find((role) => role.role_key === value) ?? null;
  const blocked = options.length === 0;

  return (
    <div className="flex flex-col gap-1">
      <Select
        id={selectId}
        aria-label={label}
        value={value}
        disabled={disabled || pending || blocked}
        onChange={(event) => {
          onChange(event.target.value);
        }}
      >
        {options.map((option) => (
          <option key={option.role_key} value={option.role_key}>
            {describeRole(option.role_key)}（{option.role_key}）
          </option>
        ))}
      </Select>
      {blocked ? (
        <p className="text-xs text-fg-muted">角色目录还没读到，暂时不能改角色。</p>
      ) : null}
      {pending ? (
        <p className="text-xs text-fg-muted" role="status">
          正在保存…
        </p>
      ) : null}
      {showPermissions && !blocked && current ? (
        <p className="text-xs text-fg-muted">{describeRolePermissions(current)}</p>
      ) : null}
    </div>
  );
}
