# ADR-0006：身份模型、角色基线与工作区行级隔离

- 日期：2026-10-28
- 状态：**提议**（未经用户/团队接受；不据此宣称技术口径已定）
- 关联需求与证据：`docs/PRD_PyKnora.md`（用户认证与权限）；`docs/plans/成员任务进度表.md` 任务 9/10/11/12/15；`docs/plans/全员模块实现路径.md` 契约 C1（`kb_repo.get(scope, kb_id)`）；`docs/tdd/07_Data_API_Security.md`（可信身份只来自 token、404 不泄漏存在性）；落地代码 `migrations/0003_identity_acl.sql`、`src/pyknora/services/auth/`、`src/pyknora/api/deps.py`。

## 背景

成员4 的五项任务（注册登录、JWT 认证、部门与角色、按身份过滤文档、KB 访问校验）共用同一套身份基座。开工前的实际状态是：`get_scope` 里 `_load_membership` 是 `return True` 的占位，任何合法签名的 token 都能进入任意工作区；仓库里没有身份表，也没有任何非 owner 的数据库角色——因此"越权测试"即使写了也永远不会失败（`FakeKbRepo` 第一版同样收了 `Scope` 就丢，见 AGENTS §8 第 4 条）。

## 决策

1. **表名用 `principals`，不用 `users`**。全仓已按 principal 口径命名（`document_versions.created_by_principal_id`、JWT `sub`），改名会让创建者溯源链断在两张表之间。身份表共 5 张：`principals`、`workspaces`、`workspace_memberships`、`roles`、`role_grants`。
2. **成员关系带属性**（`role` + `created_at`），`UNIQUE (workspace_id, user_id)`。同一人重复加入同一空间会让"这个人是不是成员"出现多条互相矛盾的答案。
3. **角色只落一个默认 `member`**，`role_grants` 留权限点表但不做多角色树、不做部门表。部门属于组织模型，在没有真实组织数据前建表只是空壳。
4. **RLS 作为兜底，而非主防线**：主防线是应用层 SQL 谓词（`workspace_id = scope.workspace_id`，见 0001 的 `v_active_chunks` 与各 repo）。RLS 只对**非 owner 角色**生效，因此新建登录角色 `pyknora_app`（无 OWNER / SUPERUSER / BYPASSRLS），业务表统一策略 `workspace_id = current_setting('app.workspace_id', true)::uuid`，未设置即 0 行（fail-closed）。`v_active_chunks` 打开 `security_invoker`，否则非 owner 读视图会绕过底表策略。
5. **身份表本身不加 RLS**：鉴权要跨工作区读成员关系（"这个人在哪些空间里"），给 `workspace_memberships` 套 workspace 策略会把自己锁死。身份表的隔离由应用层谓词负责。
6. **JWT 自实现 HS256**，claims 仅 `sub` / `default_workspace_id` / `exp`。校验时不看头部自报算法（只接受 HS256）、签名用 `hmac.compare_digest`、`sub` 必须是 UUID。401 不区分"没带 token / 过期 / 签名错"。
7. **密码只存 bcrypt**；`>72 字节`在入口 422 拒绝，不静默截断。哈希计算走 `asyncio.to_thread`，不阻塞事件循环。
8. **KB 访问校验只走契约 C1**（`kb_repo.get(scope, kb_id)`），越权与不存在同为 404。不在成员4 模块里另写一套 KB 可见性判断。
9. **每请求把 `app.workspace_id` 写进事务**（`set_config(..., true)`），且必须用**与路由同一个 session**——换成新连接/新事务，RLS 上下文立刻失效。

## 备选方案

- **应用层唯一隔离，不建 `pyknora_app` / 不启用 RLS**：实现更少，但"漏写一个 `WHERE workspace_id`"就是数据泄漏，且没有独立测试面能证明兜底存在。被否决（保留为后备：若 RLS 在真实 PG 上出现性能或兼容问题，可整体关闭策略而不改应用代码）。
- **细粒度 ACL（资源 × 主体 × 动作）**：当前只有 4 个角色、单一工作区维度，建 ACL 表会让每个查询多 2 次 join，收益为零。
- **引入第三方 JWT 库**：只支持 HS256 一种算法时，多一个依赖就多一份算法协商攻击面。
- **用 `users` 表名对齐"常规做法"**：与既有列名冲突，需同轮改成员2/5 的表与文档，属越界。
- **`FORCE ROW LEVEL SECURITY`（对 owner 也生效）**：会让迁移脚本、worker、测试夹具全部需要设置上下文，一旦漏设就静默 0 行，排障成本远高于收益。

## 后果

- 正面：越权有独立的、**能真的失败**的测试面（非 owner 角色 + 跨区 INSERT）；注册/登录/角色变更的最小闭环可用；`Scope` 的可信来源唯一。
- 负面：① `get_scope` 每请求多一次 `workspace_memberships` 查询；② RLS 对 owner（当前默认连接）无效，真正的隔离强度取决于部署时是否改用 `pyknora_app`；③ `0003` 里对尚未存在的表（`knowledge_bases`/`citations`/`index_migrations`）用 `to_regclass` 跳过，成员1 的 `0002`、成员5 的 `0004` 落地后需**重跑 0003** 才有策略；④ `pyknora_app` 的密码是开发占位，生产需外部注入并删掉迁移中的 `CREATE ROLE` 段；⑤ 若将来做成多角色树，`workspace_memberships.role` 与 `roles.role_key` 会出现两个真相源，需要一次迁移收敛。

## 验证条件

- 单元：`tests/unit/test_auth_a4.py`（载荷伪造不影响 Scope、`alg:none`/篡改签名/过期/非数字 `exp` 全部 401）、`tests/unit/test_auth_passwords.py`（bcrypt 格式、加盐、72 字节上限）。
- API（需真 PG）：`tests/api/test_auth_api.py`（201 + 只存哈希、注册 token 可用、重名 409、登录成功/失败不可区分、查询串伪造无效、角色变更越权 404、owner 不可降级）。
- 集成（需真 PG + `pyknora_app`）：`tests/integration/test_rls.py`（角色非 superuser/bypassrls/owner；跨区 0 行；未设上下文 0 行；跨区 INSERT 被拒；视图 `security_invoker`；A1 跨区检索为空）。
- 命令：`uv run python scripts/migrate.py` → `uv run pytest -q tests/api tests/integration` → 全绿。**2026-10-29 已在真库执行**：迁移退出 0（`0001` + `0003`，重复执行幂等），`tests/api` 49 passed、`tests/integration` 33 passed、`pytest -q` 全量 122 passed；库内核对到 `roles` 1 行 / `role_grants` 3 行、`pyknora_app` `rolsuper=f`/`rolbypassrls=f`、`documents`/`chunks`/`outbox` `relrowsecurity=t`、`v_active_chunks` `{security_invoker=true}`。状态仍为"提议"——验证通过不等于决策已获接受，接受权在用户/评审。

## 替代关系

无。若后续接受，本 ADR 取代 `get_scope` 的占位实现与"无身份表"的临时状态；与 ADR-0005（工作区/隐私）在 workspace 语义上互补，冲突时以本 ADR 的身份与隔离部分为准。
