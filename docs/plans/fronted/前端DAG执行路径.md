# PyKnora 前端升级 DAG 执行路径（多 agent 并行）

> 配套 `docs/plans/fronted/前端升级计划.md`（改什么见那边 §3，本文件管**顺序、并行、写范围、验收**）。
> 硬规则（用户指令）：**非单节点阶段禁止串行**，能并行的节点一次扇出；执行时按本 DAG 开多子 agent 并行。
> 状态：**W0–W4 全部完成，N8 总验收 A1–A8 全过（2026-11-05）**。W3 三路代码由 Lead 按用户授权接手收尾、W4 的 N7/N8 由 Lead 串行做完——这是对 §2 规则 1「非单节点阶段禁止串行」的有意偏差（用户 m17922「你接受之前的子agent没做完的继续做吧」），详见 §5 后的「本轮偏差与验收实录」；每完成一个节点回填 §5。

---

## 1. 节点表

| 节点 | 名称 | 目标 | 依赖 | 写范围（互斥，越界即违例） | 验收（子 agent 自证） |
|---|---|---|---|---|---|
| N0 | 基线取证 | 记录重构前四绿基线 + before 截图 5 路由 | — | `docs/plans/fronted/shots/`（只写这里） | `pnpm typecheck`/`pnpm test` 输出与截图文件落盘 |
| N1 | 设计令牌 | `@theme` 全量升级（升级计划 §2 映射表）+ Geist 自托管 + motion 令牌 | N0 | `web/app/globals.css`、`web/app/layout.tsx`、`web/app/fonts/**` | typecheck 0 错；`grep` 无字面色值泄漏；axe 不回退 |
| N2 | 基础组件 | 17 个 `components/ui/*` 外观升级（升级计划 §1.6 P1–P4） | N1 | `web/components/ui/**`、`web/lib/utils.ts` | `pnpm typecheck` 0 错、组件测试 38 条绿（只改断言） |
| N3 | 应用壳 | 顶栏/侧栏视觉、导航激活态、壳层留白 | N1 | `web/components/app-shell/**`、`web/app/**/layout.tsx` | typecheck 0 错；`nav-icons.tsx` 模式不破（RSC 边界） |
| N4 | 页面 A | login、kbs 列表、documents 列表/详情（T2/T4/L3/§1.5） | N2 | `web/app/(auth)/login/**`、`web/features/kbs/**`、`web/features/documents/**`、`web/app/(app)/kbs/page.tsx`、`web/app/(app)/kbs/[kbId]/documents/**` | typecheck 0 错 + 相关用例绿 |
| N5 | 页面 B | query、chat、members、api-docs（含引用卡/答案流层级） | N2 | `web/features/query/**`、`web/features/chat/**`、`web/features/members/**`、`web/app/(app)/api-docs/**`、`web/app/(app)/kbs/[kbId]/{query,chat}/**`、`web/app/(app)/settings/**` | 同上 |
| N6 | 交互细化 | stagger 入场、骨架 shimmer、空/错态锚点、动效收口 | N2 | `web/features/*/components/**` 的状态组件、`web/components/ui/{skeleton,empty-state,toast}.tsx` | `prefers-reduced-motion` 下无动效；typecheck 0 错 |
| N7 | 窄屏修复 | 旧账：`/kbs` 表格三档宽度被裁（1117>1024、845>390）+ 三档断言进 e2e | N4 | `web/features/kbs/KbTable.tsx`、`web/tests/e2e/**` | 390/1024/1440 三档下无横向溢出或容器可滚；e2e 全绿 |
| N8 | 总验收 | A1–A8 全量复验 + after 截图 + MVP 五步 | N4,N5,N6,N7 | 无（只跑命令与截图） | 升级计划 §5 的 A1–A8 全过 |

## 2. 波次与并行编排

```
W0  [N0]
W1  [N1]                      ← 唯一串行点：令牌定调，N2–N6 依赖它
W2  [N2] [N3]                 ← 并行 2
W3  [N4] [N5] [N6]            ← 并行 3（N4/N5/N6 写范围互斥）
W4  [N7] ─┐
      [N8] ┴─ N8 等 N4–N7 全部完成后汇合
```

编排硬规则：

1. **非单节点阶段禁止串行**：W2 必须 2 路并发、W3 必须 3 路并发、W4 的 N7 与 N8 前段并发；一次消息里同时发出同波次全部子 agent。
2. 子 agent 上限 8（运行时限制），本 DAG 峰值 3，远低于上限。
3. **写范围互斥**是并行的前置条件：任何子 agent 只许改自己范围内的文件；越界必须先向 Lead 报备。
4. 共用文件（`globals.css`、`package.json`、`next.config.ts`）**只有 N1 写**，后续节点只读。
5. 每节点完成前自证：`pnpm typecheck && pnpm test` 必须绿；失败先修自己的范围，修不动挂起报 Lead。
6. N8 由 Lead 亲跑（含起栈、MVP 五步、截图对比、进度文件回填）。
7. **重派前必须核实同节点无活实例**（2026-11-04 事故固化）：压缩笔记里的「已停止/零改动」不能当证据；必须 `list_agents` + `job_list` + 目标写范围的 `git diff --stat` + 文件 mtime 四证齐全，确认干净基线后才允许重派。本次 N2 重复派单的根因就是只信笔记、未查活实例，且停止通知（`5cfeab88` 实为 N3）被过度泛化。
8. 模型/会话切换后，旧会话派出的后台实例在新会话里不可见（`send_message`/`job_kill` 会报 not found）——**收敛重复实例须由用户在 GUI 侧停掉**，Lead 不得凭空假定「已停」。

## 3. 写范围互斥总表（速查）

| 目录/文件 | 归属 |
|---|---|
| `web/app/globals.css`、`web/app/layout.tsx`、`web/app/fonts/**` | N1 |
| `web/components/ui/**`、`web/lib/utils.ts` | N2（N6 只许动 skeleton/empty-state/toast 三个文件） |
| `web/components/app-shell/**`、`web/app/**/layout.tsx` | N3 |
| `web/app/(auth)/login/**`、`web/features/kbs/**`、`web/features/documents/**`、`kbs/page.tsx`、`kbs/[kbId]/documents/**` | N4（N7 只许动 `KbTable.tsx`） |
| `web/features/{query,chat,members}/**`、`api-docs/**`、`kbs/[kbId]/{query,chat}/**`、`settings/**` | N5 |
| `web/features/*/components/**`（状态组件）、`ui/{skeleton,empty-state,toast}.tsx` | N6 |
| `web/tests/e2e/**` | N7 |
| `docs/plans/fronted/shots/**` | N0（before）、N8（after） |
| `lib/api/**`、`lib/contracts/**`、`web/tests/contract/**` | **谁都只读** |

## 4. 子 agent 任务模板（Lead 派单时用）

每个子 agent 的 prompt 必须包含：① 节点号与目标；② 写范围（上表原文）；③ 升级计划 §2 的令牌映射表（最终值，供 className 引用）；④ 红线（语义令牌、对比度、focus-visible、reduced-motion、状态双通道）；⑤ 自证命令（`pnpm typecheck && pnpm test`）；⑥ 回报格式（改了什么/命令输出/没做什么/风险）。

## 5. 节点状态回填

| 节点 | 状态 | 备注 |
|---|---|---|
| N0 | **已完成** | 基线绿（typecheck 0 错 / vitest 250 passed）；before 5 张截图落 `docs/plans/fronted/shots/`（KB `a93caafb`，截图账号已注册） |
| N1 | **已完成** | 令牌落定（Geist 自托管实证 5 woff2、shadow-sm/md/lg 着色值进产物）；偏差裁决：shadow 只用 sm/md/lg、`outline-primary`→`outline-primary-ring` 由 N2 收。**补修（Lead 2026-11-05）**：`--duration-fast/base/slow` 原是 no-op（Tailwind v4 读 `--transition-duration-*` 命名空间），改名后 `pnpm build` 产物实证生成 `.duration-fast{--tw-duration:var(--transition-duration-fast)}`，N2/N3 的过渡令牌真正生效；三证全绿 |
| N2 | **已完成** | 唯一实例 `e5fd94c2` 自行跑完（12 改 + 新增 `ui-motion.module.css`，props/DOM 零改动）；三证全绿 typecheck 0 / vitest 250 / build 成功；9 处焦点环收口、`ui/` 字面色值零命中（shimmer 高光改 `color-mix` 语义令牌）、Badge 方角化补齐、测试断言一条未动。Lead 误判「无活实例」时曾落一批 ui/ 编辑，已被该实例审计合并，归属清晰；dialog 遮罩取 `bg-fg/20 backdrop-blur-[2px]`（brief 的 4% 实测过淡） |
| N3 | **已完成** | `n3-shell` 三证全绿：typecheck 0 / vitest 250 / build 成功（7 改 2 新增，RSC 边界未破）；`shell-motion.ts` 的 TS2322 已自修为 `styles.enter ?? ""` |
| N4 | **已完成** | W3 三路并行落盘（login/kbs/documents 页头 T2/T4、表格 L3、§1.5 文案、KbTable/DocumentTable/UploadPanel/ChunkList/LineageTimeline/MemberTable）；代码由子 agent 落盘、自证反复失败后经用户授权由 Lead 接手收口；`ChatComposer.tsx:89` 裸 `outline-primary` 收口为 `outline-primary-ring`；三证全绿 |
| N5 | **已完成** | W3 三路并行落盘（query/chat/members/api-docs；CitationRail/StreamingAnswer/ChatTurn/AnswerStatus/DegradedNote/ChatScreen/QueryConsole/errors/RetrievalTable 全系列）；SSE 四帧、`done.invalid_citations` 整数、`[资料 N]` 格式、游标文案「分页游标已过期」等契约零改动；三证全绿 |
| N6 | **已完成** | 状态组件 + `ui/{skeleton,empty-state,toast}.tsx` 三文件细化；核实 N2 已做完 shimmer/空态锚点/入场，不重做、保留 `styles.x ?? ""` 兜底与 `animate-pulse`、`prefers-reduced-motion` 关闭在位；三证全绿 |
| N7 | **已完成** | 按 L4 实测校正口径（表格本就可横向滚动，旧账是量错对象）**不卡片化/不重排**：`KbTable.tsx` 加 `xl:hidden` 窄屏「表格可左右滑动」提示；新增 `web/tests/e2e/responsive.spec.ts` 三档（1440/1024/390）断言页面零横向溢出 + 表格滚到最右最后一列可到达 + 窄档提示可见；修 `kbs.spec.ts` 两处 `getByRole("link",{name})` → `exact:true`（W3 新增「进入」列 aria-label 含库名致 strict mode 命中 4 元素）；e2e 13 passed / exit 0 |
| N8 | **已完成** | Lead 亲跑 A1–A8：typecheck 0 / vitest 250(32 文件) / build 11 路由；e2e **13 passed**（含新增宽度用例）+ axe 5 路由 critical=0 serious=0；before/after 各 5 张截图落 `shots/`，像素级客观对照 changed_px 7.0%–23.5%（documents 页高 900→919 布局重排）；A7 MVP 五步由 13 条 e2e 全链真跑覆盖（注册→建库→上传 202→轮询 succeeded→检索→流式问答）；A8 `lib/api`/`lib/contracts` 零改动。遗留裁决：`skip-link.tsx:13` 的 `duration-150`→`duration-fast`（全仓唯一硬编码时长，值同为 150ms、零视觉差异） |

## 5.1 本轮偏差与验收实录（2026-11-05，Lead）

- **对 §2 规则 1 的有意偏差**：W3 的 N4/N5/N6 三路确按规则并行派出（一次扇出 3 路，写范围互斥），但三路把 `typecheck`/`test` 自证派给内部 background subagent 反复 failed；用户连续纠正口径后以 m17922「你接受之前的子agent没做完的继续做吧」为准，由 **Lead 本人接手三路收尾**，W4 的 N7/N8 也由 Lead 串行做完，未再派新实例。偏差原因是子 agent 无法续跑其已失败的后台自证，且模型/会话切换后旧会话实例在新会话不可见（§2 规则 8）。
- **A1–A8 终验输出（真实命令 + 结果）**：
  - A1 `pnpm typecheck` → `TYPECHECK_EXIT=0`；
  - A2 `pnpm test` → `Test Files 32 passed (32) / Tests 250 passed (250)`、`TEST_EXIT=0`；
  - A3 `pnpm build` → `✓ Compiled successfully in 1595ms`、11 路由、`BUILD_EXIT=0`；
  - A4 `pnpm test:e2e` 第二轮 → **13 passed / exit 0 / 1.3m**（a11y 5 + auth 2 + chat 1 + documents 1 + kbs 2 + query 1 + responsive 1）；
  - A5 axe 5 路由全 `critical=0 serious=0 moderate=0 minor=0`；
  - A6 before/after 各 5 张（共 10 张）落 `docs/plans/fronted/shots/`，Pillow+numpy 像素对比 5 页全部有实质变化；
  - A7 e2e 13 条覆盖注册→建库→上传→检索→流式问答全链（真后端 uvicorn 8000 + 假 SSE 上游 8123 + `next start` 3000）；
  - A8 `git diff --stat -- web/lib/api web/lib/contracts` 空、状态零改动（`A8_CLEAN=True`）。
- **红线复核**：字面色值（排除 globals.css 令牌定义）0 命中；裸 `outline-primary`（非 -ring）0 命中；契约类（`animate-pulse`/`size-11`/`min-h-hit`/`overflow-x-auto`/`h-4`）保留；CSS Module 的 `prefers-reduced-motion` 关闭在位。
- **环境**：起 `pyknora-pg-p0`（宿主 5434，17 表）与 Docker Desktop daemon 作 e2e/截图前置；收尾 `job_kill dev_up.py` 后 8010/3000/8000/8123 全 free、临时脚本 `.tmp-shots-before.mjs` 与 `.tmp_ref/verify-duration.mjs` 已删（0 残留）。

## 6. 失败与回滚

- 每节点独立成段：`pnpm typecheck && pnpm test` 绿才进下一轮；单节点失败不牵连别波（写范围互斥保证 revert 粒度就是该节点的文件面）。
- 视觉回退判断：N8 的 before/after 截图对照 + axe 输出；**发现对比度/focus 违规直接判红**，不接受"看着还行"。
- 与后端无关，无需回滚后端；`web/.next` 与 `dist` 均可重建。
