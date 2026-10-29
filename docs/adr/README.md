# 架构决策记录

本目录保存 ADR。2026-10-27 新增五份编号 ADR，2026-10-28 新增 0006；状态均为**提议**，当前没有已接受 ADR；用户规范和 PRD 的既定职责继续适用。

| 编号 | 主题 | 状态 |
| --- | --- | --- |
| [0001](0001_Runtime_Boundaries.md) | 运行时职责与状态边界 | 提议 |
| [0002](0002_Outbox_Version_Publication.md) | 可靠派发与索引版本发布 | 提议 |
| [0003](0003_Approval_Side_Effects.md) | 审批、interrupt 与副作用核查 | 提议 |
| [0004](0004_Parser_Browser_Reuse.md) | DocReader 与浏览器双适配复用 | 提议 |
| [0005](0005_Workspace_Events_Privacy.md) | 工作区、引用、事件与隐私 | 提议 |
| [0006](0006_Identity_Roles_RLS.md) | 身份模型、角色基线与工作区行级隔离 | 提议 |

FTS/BM25 选型、Wiki 优先级及产品未决项见 [总体 TDD 决策清单](../tdd/01_System_Architecture.md)。

使用 `NNNN_主题.md` 命名，编号递增。每份包含：日期、状态（提议/已接受/已替代/已撤销）、关联需求与证据、背景、决策、备选方案、后果、验证条件及替代关系。已接受记录发生变化时新增 ADR 并关联旧记录。

待评审：上述六份提议的验证与接受条件；FTS/BM25 选择待 H6。新增决策继续编号，不把设计提议自动升级为已接受。0006 的验证命令（迁移 + `tests/api` + `tests/integration`）已于 2026-10-29 在真库执行通过（122 passed），但状态仍是"提议"，等待评审接受。
