/**
 * `@/components/ui` —— N3 基础组件库的唯一入口。
 *
 * 约束（计划 §4 / DAG §5.2）：
 * - 组件只处理外观与通用交互，不发请求、不读业务上下文；
 * - 只用 `globals.css` 的语义令牌与 Tailwind 类，不出现十六进制色值；
 * - 触控目标 ≥ 44px（`min-h-hit`），焦点可见，过渡 150–250ms。
 */
export {
  Button,
  buttonVariants,
  type ButtonProps,
  type ButtonSize,
  type ButtonVariant,
} from "./button";

export { Field, type FieldProps } from "./field";
export { Input, type InputProps } from "./input";
export { Label, type LabelProps } from "./label";
export { Select, type SelectProps } from "./select";
export { Textarea, type TextareaProps } from "./textarea";

export {
  Dialog,
  DialogContent,
  DialogTrigger,
  type DialogContentProps,
  type DialogWidth,
} from "./dialog";
export { Tabs, TabsContent, TabsList, TabsTrigger } from "./tabs";

export {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  type TableProps,
} from "./table";

export { Badge, badgeVariants, type BadgeProps, type BadgeTone } from "./badge";
export { StatusBadge, type StatusBadgeProps } from "./status-badge";

export { Card, CardContent, CardHeader, CardTitle } from "./card";
export { EmptyState, type EmptyStateProps } from "./empty-state";
export { Skeleton } from "./skeleton";

export {
  Toast,
  ToastProvider,
  useToast,
  type ToastApi,
  type ToastOptions,
  type ToastProps,
  type ToastProviderProps,
  type ToastRecord,
  type ToastTone,
} from "./toast";
