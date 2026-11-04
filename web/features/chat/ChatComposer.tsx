"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

/**
 * 输入区：**发送**与**停止**。
 *
 * 停止按钮只在流还开着时出现，且它是"中止请求"，不是"暂停生成"——后端没有续传
 * 语义（`CANCELLED` 状态本轮都没实现），按下去就等价于这一轮到此为止，已收到的
 * 文字保留并标记不完整。所以文案用"停止"，不用"暂停"。
 *
 * 键盘：Enter 发送、Shift+Enter 换行。用 `isComposing` 挡掉输入法合成回车的经典 bug
 * ——中文输入法里选中候选词按回车，如果不过滤，会把半截拼音直接发出去。
 */
export interface ChatComposerProps {
  readonly onSubmit: (query: string) => void;
  readonly onStop: () => void;
  /** 流还开着：发送禁用、显示停止。 */
  readonly busy: boolean;
  /** 额外禁用（例如缺少知识库参数）。 */
  readonly disabled?: boolean;
  readonly className?: string;
}

export function ChatComposer({
  onSubmit,
  onStop,
  busy,
  disabled = false,
  className,
}: ChatComposerProps): React.ReactElement {
  const [value, setValue] = React.useState("");
  const inputId = React.useId();
  const trimmed = value.trim();
  const blocked = disabled;

  const submit = React.useCallback(() => {
    if (blocked || busy) {
      return;
    }
    const query = value.trim();
    if (query === "") {
      return;
    }
    setValue("");
    onSubmit(query);
  }, [blocked, busy, onSubmit, value]);

  const handleKeyDown = React.useCallback<React.KeyboardEventHandler<HTMLTextAreaElement>>(
    (event) => {
      if (event.key !== "Enter" || event.shiftKey) {
        return;
      }
      // 输入法正在合成时按回车是"选词"，不是"发送"。
      if (event.nativeEvent.isComposing) {
        return;
      }
      event.preventDefault();
      submit();
    },
    [submit],
  );

  return (
    <form
      className={className}
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <label htmlFor={inputId} className="sr-only">
        问题
      </label>
      <div className="rounded-lg border border-border bg-surface p-2 shadow-sm">
        <Textarea
          id={inputId}
          rows={3}
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
          }}
          onKeyDown={handleKeyDown}
          placeholder="就当前知识库提问，例如「电源接反会怎样？」"
          disabled={blocked}
          className="min-h-20 border-0 bg-transparent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        />
        <div className="flex flex-wrap items-center justify-between gap-2 pt-2">
          <p className="text-[11px] text-fg-subtle">
            Enter 发送 · Shift+Enter 换行 · 回答逐字流出，停止后不会续传
          </p>
          <div className="flex items-center gap-2">
            {busy ? (
              <Button type="button" variant="secondary" onClick={onStop}>
                停止
              </Button>
            ) : (
              <Button type="submit" disabled={blocked || trimmed === ""}>
                发送
              </Button>
            )}
          </div>
        </div>
      </div>
    </form>
  );
}
