"use client";

import { useId, useState, type FormEvent } from "react";
import {
  Button,
  Dialog,
  DialogContent,
  DialogTrigger,
  Field,
  Input,
  Label,
  type ButtonVariant,
} from "@/components/ui";
import type { CreateKbInput } from "./api";
import { describeKbError } from "./errors";
import { useCreateKb, type KbWorkspaceId } from "./hooks";

/**
 * 新建知识库。
 *
 * 表单里的五项（模型 / 维度 / 切片长度 / 重叠 / 归一化）在创建时**固化**进 `profile`，
 * 之后 PATCH 只能改名，所以这里把默认值直接做成可读常量，页面上也点明"创建后不可改"。
 */

/** 与后端 `schema` 的默认值口径一致；导出是为了让用例与将来"克隆知识库"复用它。 */
export const DEFAULT_KB_PROFILE = {
  embedding_model_key: "BAAI/bge-small-zh-v1.5",
  embedding_dim: 512,
  chunk_size: 512,
  chunk_overlap: 64,
  normalize: true,
} as const;

/** 后端 `KnowledgeBaseCreate.name`：1–200 字符。 */
const NAME_MAX_LENGTH = 200;

interface FormState {
  name: string;
  embeddingModelKey: string;
  embeddingDim: string;
  chunkSize: string;
  chunkOverlap: string;
  normalize: boolean;
}

interface FormErrors {
  name?: string;
  embeddingModelKey?: string;
  embeddingDim?: string;
  chunkSize?: string;
  chunkOverlap?: string;
}

const EMPTY_FORM: FormState = {
  name: "",
  embeddingModelKey: DEFAULT_KB_PROFILE.embedding_model_key,
  embeddingDim: String(DEFAULT_KB_PROFILE.embedding_dim),
  chunkSize: String(DEFAULT_KB_PROFILE.chunk_size),
  chunkOverlap: String(DEFAULT_KB_PROFILE.chunk_overlap),
  normalize: DEFAULT_KB_PROFILE.normalize,
};

function isPositiveInteger(value: number): boolean {
  return Number.isInteger(value) && value > 0;
}

function isNonNegativeInteger(value: number): boolean {
  return Number.isInteger(value) && value >= 0;
}

/**
 * 一次性校验并转成请求体：数字字段用字符串 state（`<input type="number">` 的天然形态），
 * 提交前再 `Number(...)`，避免"空串被当成 0""小数被静默截断"。
 */
function validate(form: FormState): { errors: FormErrors; input: CreateKbInput | null } {
  const errors: FormErrors = {};

  const name = form.name.trim();
  if (name.length === 0) errors.name = "请输入知识库名称。";
  else if (name.length > NAME_MAX_LENGTH) errors.name = `名称不能超过 ${NAME_MAX_LENGTH} 个字符。`;

  const embeddingModelKey = form.embeddingModelKey.trim();
  if (embeddingModelKey.length === 0) errors.embeddingModelKey = "请填写嵌入模型 key。";

  const embeddingDim = Number(form.embeddingDim);
  const chunkSize = Number(form.chunkSize);
  const chunkOverlap = Number(form.chunkOverlap);

  if (!isPositiveInteger(embeddingDim)) errors.embeddingDim = "维度必须是正整数。";
  if (!isPositiveInteger(chunkSize)) errors.chunkSize = "切片长度必须是正整数。";
  if (!isNonNegativeInteger(chunkOverlap)) errors.chunkOverlap = "重叠长度必须是非负整数。";
  else if (isPositiveInteger(chunkSize) && chunkOverlap >= chunkSize) {
    errors.chunkOverlap = "重叠长度必须小于切片长度。";
  }

  if (Object.keys(errors).length > 0) return { errors, input: null };

  return {
    errors,
    input: {
      name,
      embedding_model_key: embeddingModelKey,
      embedding_dim: embeddingDim,
      chunk_size: chunkSize,
      chunk_overlap: chunkOverlap,
      normalize: form.normalize,
    },
  };
}

export interface KbCreateDialogProps {
  workspaceId?: KbWorkspaceId;
  triggerLabel?: string;
  triggerVariant?: ButtonVariant;
}

export function KbCreateDialog({
  workspaceId = null,
  triggerLabel = "新建知识库",
  triggerVariant = "primary",
}: KbCreateDialogProps) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [errors, setErrors] = useState<FormErrors>({});
  const [submitError, setSubmitError] = useState<string | null>(null);
  const idPrefix = useId();
  const create = useCreateKb(workspaceId);

  function handleOpenChange(nextOpen: boolean) {
    setOpen(nextOpen);
    if (nextOpen) {
      setForm(EMPTY_FORM);
      setErrors({});
      setSubmitError(null);
    }
  }

  function updateField<K extends keyof FormState>(patch: Pick<FormState, K>, errorKey?: keyof FormErrors) {
    setForm((current) => ({ ...current, ...patch }));
    if (!errorKey) return;
    setErrors((current) => (current[errorKey] === undefined ? current : { ...current, [errorKey]: undefined }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const { errors: nextErrors, input } = validate(form);
    setErrors(nextErrors);
    if (!input) return;

    setSubmitError(null);
    try {
      await create.mutateAsync(input);
      setOpen(false);
    } catch (error) {
      setSubmitError(describeKbError(error, "create"));
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button variant={triggerVariant}>{triggerLabel}</Button>
      </DialogTrigger>
      <DialogContent
        title="新建知识库"
        description="嵌入模型与切片参数在创建时固化，之后只能改名；要换模型请新建一个知识库。"
        width="md"
      >
        <form className="flex flex-col gap-4" onSubmit={handleSubmit} noValidate>
          <Field
            id={`${idPrefix}-name`}
            label="名称"
            required
            hint={`1–${NAME_MAX_LENGTH} 个字符。`}
            error={errors.name}
          >
            <Input
              value={form.name}
              onChange={(event) => updateField({ name: event.target.value }, "name")}
              placeholder="例如：产品手册"
              autoComplete="off"
              autoFocus
            />
          </Field>

          <Field
            id={`${idPrefix}-model`}
            label="嵌入模型"
            required
            hint="HuggingFace 模型 key，例如 BAAI/bge-small-zh-v1.5。"
            error={errors.embeddingModelKey}
          >
            <Input
              value={form.embeddingModelKey}
              onChange={(event) =>
                updateField({ embeddingModelKey: event.target.value }, "embeddingModelKey")
              }
              autoComplete="off"
              spellCheck={false}
            />
          </Field>

          <div className="grid gap-4 sm:grid-cols-3">
            <Field
              id={`${idPrefix}-dim`}
              label="向量维度"
              required
              error={errors.embeddingDim}
            >
              <Input
                type="number"
                inputMode="numeric"
                min={1}
                step={1}
                value={form.embeddingDim}
                onChange={(event) => updateField({ embeddingDim: event.target.value }, "embeddingDim")}
              />
            </Field>
            <Field
              id={`${idPrefix}-size`}
              label="切片长度"
              required
              error={errors.chunkSize}
            >
              <Input
                type="number"
                inputMode="numeric"
                min={1}
                step={1}
                value={form.chunkSize}
                onChange={(event) => updateField({ chunkSize: event.target.value }, "chunkSize")}
              />
            </Field>
            <Field
              id={`${idPrefix}-overlap`}
              label="切片重叠"
              required
              hint="必须小于切片长度。"
              error={errors.chunkOverlap}
            >
              <Input
                type="number"
                inputMode="numeric"
                min={0}
                step={1}
                value={form.chunkOverlap}
                onChange={(event) => updateField({ chunkOverlap: event.target.value }, "chunkOverlap")}
              />
            </Field>
          </div>

          <div className="flex items-start gap-3 rounded-md border border-border bg-surface-muted px-3 py-2">
            <input
              id={`${idPrefix}-normalize`}
              type="checkbox"
              checked={form.normalize}
              onChange={(event) => updateField({ normalize: event.target.checked })}
              className="mt-1 h-4 w-4 accent-primary"
            />
            <div className="flex flex-col gap-0.5">
              <Label htmlFor={`${idPrefix}-normalize`}>归一化向量</Label>
              <p className="text-xs text-fg-muted">
                启用后按 L2 归一化写入，余弦相似度检索更稳定。
              </p>
            </div>
          </div>

          {submitError ? (
            <p role="alert" className="text-sm text-danger">
              {submitError}
            </p>
          ) : null}

          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setOpen(false)}>
              取消
            </Button>
            <Button type="submit" loading={create.isPending}>
              创建
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
