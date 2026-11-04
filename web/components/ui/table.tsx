import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Table —— 语义化 `<table>`（`thead/tbody/tr/th/td`），不是 div 拼的网格：
 * 读屏能报出"第几行第几列"，浏览器原生支持表格导航快捷键。
 *
 * `Table` 外面包一层横向滚动容器（`containerClassName` 可覆盖）：宽表在 390px
 * 宽度下只该在容器内滚动，不能把整页撑出横向溢出（总验收项之一）。
 */
export interface TableProps extends React.TableHTMLAttributes<HTMLTableElement> {
  containerClassName?: string;
}

export const Table = React.forwardRef<HTMLTableElement, TableProps>(function Table(
  { className, containerClassName, ...props },
  ref,
) {
  return (
    <div className={cn("w-full overflow-x-auto", containerClassName)}>
      <table
        ref={ref}
        className={cn("w-full border-collapse text-sm text-fg", className)}
        {...props}
      />
    </div>
  );
});

Table.displayName = "Table";

export const TableHeader = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(function TableHeader({ className, ...props }, ref) {
  return (
    <thead
      ref={ref}
      className={cn("border-b border-border bg-surface-muted [&_tr]:border-0", className)}
      {...props}
    />
  );
});

TableHeader.displayName = "TableHeader";

export const TableBody = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(function TableBody({ className, ...props }, ref) {
  return (
    <tbody
      ref={ref}
      className={cn("[&_tr:last-child]:border-0", className)}
      {...props}
    />
  );
});

TableBody.displayName = "TableBody";

export const TableRow = React.forwardRef<
  HTMLTableRowElement,
  React.HTMLAttributes<HTMLTableRowElement>
>(function TableRow({ className, ...props }, ref) {
  return (
    <tr
      ref={ref}
      className={cn(
        "border-b border-border transition-colors duration-150 ease-out hover:bg-surface-muted",
        "data-[state=selected]:bg-primary-weak",
        className,
      )}
      {...props}
    />
  );
});

TableRow.displayName = "TableRow";

export const TableHead = React.forwardRef<
  HTMLTableCellElement,
  React.ThHTMLAttributes<HTMLTableCellElement>
>(function TableHead({ className, scope = "col", ...props }, ref) {
  return (
    <th
      ref={ref}
      scope={scope}
      className={cn(
        "px-3 py-2 text-left align-middle text-xs font-semibold tracking-wide text-fg-muted uppercase",
        className,
      )}
      {...props}
    />
  );
});

TableHead.displayName = "TableHead";

export const TableCell = React.forwardRef<
  HTMLTableCellElement,
  React.TdHTMLAttributes<HTMLTableCellElement>
>(function TableCell({ className, ...props }, ref) {
  return (
    <td ref={ref} className={cn("px-3 py-2 align-middle", className)} {...props} />
  );
});

TableCell.displayName = "TableCell";
