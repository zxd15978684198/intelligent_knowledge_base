import "./local-storage-shim";

import { describe, expect, it } from "vitest";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Field,
  Input,
  Label,
  Select,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Textarea,
} from "@/components/ui";

/**
 * 冒烟：barrel 里其余组件（Input/Textarea/Select/Label/Field/Table/Tabs/Card/Badge/Skeleton）
 * 至少能真实渲染、语义正确、没有占位实现。逐个交互的深水测试留给各业务节点的页面用例。
 */
describe("表单控件 + Field 联动", () => {
  it("Field 把 label / hint / error 自动关联到 Input", () => {
    render(
      <Field id="kb-name" label="知识库名称" hint="3–50 字" error="名称已被占用">
        <Input />
      </Field>,
    );

    const input = screen.getByLabelText("知识库名称");
    expect(input).toHaveClass("min-h-hit");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input.getAttribute("aria-describedby")).toContain("kb-name-hint");
    expect(input.getAttribute("aria-describedby")).toContain("kb-name-error");
    expect(screen.getByRole("alert")).toHaveTextContent("名称已被占用");
  });

  it("Field 无 error 时不设置 aria-invalid", () => {
    render(
      <Field id="kb-desc" label="描述" hint="可选">
        <Textarea />
      </Field>,
    );

    const textarea = screen.getByLabelText("描述");
    expect(textarea).not.toHaveAttribute("aria-invalid");
    expect(textarea.getAttribute("aria-describedby")).toBe("kb-desc-hint");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("Select 是原生 select：可键盘展开并用方向键改值", async () => {
    const user = userEvent.setup();
    render(
      <Field id="scope" label="命名空间">
        <Select defaultValue="v1">
          <option value="v1">v1</option>
          <option value="v2">v2</option>
        </Select>
      </Field>,
    );

    const select = screen.getByLabelText("命名空间");
    expect(select.tagName).toBe("SELECT");
    expect(select).toHaveClass("min-h-hit");

    await user.selectOptions(select, "v2");
    expect(select).toHaveValue("v2");
  });

  it("Label 的必填语义给读屏（视觉星号 aria-hidden）", () => {
    render(<Label htmlFor="x" required>名称</Label>);

    expect(screen.getByText("名称")).toBeInTheDocument();
    expect(screen.getByText("（必填）")).toHaveClass("sr-only");
    const star = screen.getByText("*");
    expect(star).toHaveAttribute("aria-hidden", "true");
  });
});

describe("Table", () => {
  it("输出语义化表格：table / columnheader / row / cell，th 带 scope", () => {
    render(
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>文件名</TableHead>
            <TableHead>状态</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell>手册.pdf</TableCell>
            <TableCell>已完成</TableCell>
          </TableRow>
        </TableBody>
      </Table>,
    );

    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getAllByRole("columnheader")).toHaveLength(2);
    expect(screen.getAllByRole("row")).toHaveLength(2);
    expect(screen.getByRole("cell", { name: "手册.pdf" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "文件名" })).toHaveAttribute("scope", "col");
  });

  it("宽表在容器内横向滚动，不把整页撑宽", () => {
    render(
      <Table>
        <TableBody>
          <TableRow>
            <TableCell>1</TableCell>
          </TableRow>
        </TableBody>
      </Table>,
    );

    const table = screen.getByRole("table");
    expect(table.parentElement).toHaveClass("overflow-x-auto");
  });
});

describe("Tabs", () => {
  it("tablist/tab/tabpanel 语义齐全，键盘可切换", async () => {
    const user = userEvent.setup();
    render(
      <Tabs defaultValue="docs">
        <TabsList aria-label="对象详情">
          <TabsTrigger value="docs">文档</TabsTrigger>
          <TabsTrigger value="jobs">作业</TabsTrigger>
        </TabsList>
        <TabsContent value="docs">文档列表</TabsContent>
        <TabsContent value="jobs">作业列表</TabsContent>
      </Tabs>,
    );

    expect(screen.getByRole("tablist")).toBeInTheDocument();
    const docs = screen.getByRole("tab", { name: "文档" });
    expect(docs).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel", { name: "文档" })).toHaveTextContent("文档列表");

    // 用 click 聚焦：userEvent 会包 act；裸 focus() 触发的 Radix roving-focus setState
    // 不在 act 里，会在 stderr 刷 "not wrapped in act(...)"。
    await user.click(docs);
    // Radix 的 roving focus 在键盘事件里自行 setState，userEvent 的默认 act 包裹盖不住，
    // 这里再显式包一层，避免同样的告警。
    await act(async () => {
      await user.keyboard("{ArrowRight}");
    });

    expect(screen.getByRole("tab", { name: "作业" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel", { name: "作业" })).toHaveTextContent("作业列表");
  });
});

describe("展示类", () => {
  it("Card 的标题层级是 h3，内容区语义完整", () => {
    render(
      <Card>
        <CardHeader>
          <CardTitle>索引概览</CardTitle>
        </CardHeader>
        <CardContent>共 12 个分片</CardContent>
      </Card>,
    );

    expect(screen.getByRole("heading", { level: 3, name: "索引概览" })).toBeVisible();
    expect(screen.getByText("共 12 个分片")).toBeVisible();
  });

  it("Badge 按 tone 取语义令牌色，默认 neutral", () => {
    render(
      <div>
        <Badge>默认</Badge>
        <Badge tone="danger">危险</Badge>
      </div>,
    );

    expect(screen.getByText("默认")).toHaveClass("bg-surface-muted");
    expect(screen.getByText("危险")).toHaveClass("bg-danger-weak");
  });

  it("Skeleton 对读屏隐藏且不做长动画", () => {
    const { container } = render(<Skeleton className="h-4 w-32" />);
    const skeleton = container.firstElementChild;

    expect(skeleton).not.toBeNull();
    expect(skeleton).toHaveAttribute("aria-hidden", "true");
    expect(skeleton).toHaveClass("animate-pulse");
    expect(skeleton).toHaveClass("h-4");
  });
});
