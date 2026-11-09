import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import {
  AnswerMarkup,
  parseAnswerBlocks,
  splitInline,
  stripDanglingMarkup,
} from "@/features/chat/answer-markup";

/**
 * 正文渲染的单元用例。
 *
 * 这一组守的是两件曾经出错的事：模型写的 `**粗体**` / `## 标题` / `- 条目`
 * 必须变成元素而不是原样显示；`[资料 N]` 必须仍然是按钮，且序号、越界标记不变。
 */

function renderMarkup(text: string, streaming = false): HTMLElement {
  const { container } = render(
    <AnswerMarkup
      text={stripDanglingMarkup(text, streaming)}
      invalidIndices={new Set<number>()}
      activeCitation={null}
      onPickCitation={vi.fn()}
      onRegisterMarker={vi.fn()}
    />,
  );
  return container;
}

describe("features/chat/answer-markup", () => {
  it("把标题、无序列表、有序列表、段落切成块", () => {
    const blocks = parseAnswerBlocks(
      ["## 一、适用条件", "- 与配偶分居两地", "- 未婚且父母在外地", "", "1. 填写申请单", "2. 部门审批", "", "正文段落"].join(
        "\n",
      ),
    );
    expect(blocks.map((block) => block.kind)).toEqual([
      "heading",
      "list",
      "list",
      "paragraph",
    ]);
    const heading = blocks[0];
    expect(heading?.kind === "heading" ? heading.text : "").toBe("一、适用条件");
    const bullet = blocks[1];
    expect(bullet?.kind === "list" ? bullet.items : []).toEqual([
      "与配偶分居两地",
      "未婚且父母在外地",
    ]);
    const ordered = blocks[2];
    expect(ordered?.kind === "list" ? ordered.ordered : false).toBe(true);
  });

  it("行内只认粗体与行内代码，其余原样保留", () => {
    expect(splitInline("**办理手续**：先填表，再走 `OA` 流程")).toEqual([
      { kind: "strong", text: "办理手续" },
      { kind: "text", text: "：先填表，再走 " },
      { kind: "code", text: "OA" },
      { kind: "text", text: " 流程" },
    ]);
  });

  it("流式中把未闭合的标记藏起来，结束后原样显示", () => {
    expect(stripDanglingMarkup("先填**申请", true)).toBe("先填");
    expect(stripDanglingMarkup("先填**申请**", true)).toBe("先填**申请**");
    // 流结束还缺闭合，是模型的问题，不该由界面掩盖。
    expect(stripDanglingMarkup("先填**申请", false)).toBe("先填**申请");
  });

  it("渲染成元素：粗体是 strong、列表是 ul/li，正文里不残留 ** 标记", () => {
    const body = renderMarkup(
      ["## 适用条件", "- **配偶**分居两地", "每年可享受 **30 天** [资料 1]"].join("\n"),
    );

    expect(body.querySelectorAll("strong").length).toBe(2);
    expect(body.querySelectorAll("ul > li").length).toBe(1);
    expect(body.textContent).toContain("配偶");
    expect(body.textContent).not.toContain("**");
    // 标题不显示 `##`，列表项不显示 `-`。
    expect(body.textContent).not.toContain("##");
    expect(body.textContent).not.toContain("- 配偶");
  });

  it("引用标记仍然是按钮，序号与原文一致", () => {
    const onPick = vi.fn();
    render(
      <AnswerMarkup
        text="按规定执行。[资料 2]"
        invalidIndices={new Set<number>()}
        activeCitation={null}
        onPickCitation={onPick}
        onRegisterMarker={vi.fn()}
      />,
    );
    const marker = screen.getByRole("button", { name: "[资料 2]" });
    expect(marker).toHaveAttribute("data-citation-index", "2");
    marker.click();
    expect(onPick).toHaveBeenCalledWith(2);
  });
});
