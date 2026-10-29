-- 0006_role_grants.sql
-- 把 `role_grants` 从"建了但没人读"变成**判权的真相源**。
--
-- 背景：0003 只落了 `member` 一个角色和 3 个只读权限点，代码侧判权用的是
-- `workspaces.py` 里的 `_ADMIN_ROLES` 闭集，`role_grants` 是一张空壳表。
-- 本次接入后，运行期判权 = `workspace_memberships.role` → `roles` → `role_grants`
-- → 权限点集合（见 `src/pyknora/services/auth/permissions.py`）。
--
-- 号段说明（越界，需成员5 追认）：`0004`（索引迁移）归成员5，`0005` 已被
-- 主键修复占用并待追认；本次由用户明确要求「落地 role_grants」，故占 `0006`，
-- `0007+` 归位。
--
-- 幂等：全部 ON CONFLICT DO NOTHING，可重复执行。

COMMENT ON TABLE role_grants IS
    '角色→权限点。运行期判权只读这张表（+ roles）；角色闭集见 workspace_memberships.role 的 CHECK';
COMMENT ON COLUMN role_grants.permission IS
    '权限点，合法取值见 src/pyknora/services/auth/permissions.py 的 ALL_PERMISSIONS';

-- ---------- 1. 补齐角色（0003 只建了 member；四个角色都要在） ----------
-- 这里连 `member` 一起 INSERT：只补三个的话，任何"清过 roles 表"的库（测试夹具、
-- 手工 TRUNCATE）都会缺 member，而缺了它的表现是**普通成员一律 403**——权限矩阵
-- 本身没错，只是那一行角色不存在，join 直接落空。
INSERT INTO roles (role_key, description) VALUES
    ('owner', '工作区所有者：全部权限'),
    ('admin', '管理员：全部权限（不含转移所有权）'),
    ('member', '默认成员角色：业务读写 + 检索问答，不能改成员与角色'),
    ('guest', '访客：只读，不能写入或触发重建')
ON CONFLICT (role_key) DO UPDATE SET description = EXCLUDED.description;

-- ---------- 2. 权限矩阵 ----------
-- 每一行 = 一个权限点 + 拥有它的角色。与 permissions.py 的 SEED 一一对应。
INSERT INTO role_grants (role_id, permission)
SELECT r.role_id, p.permission
FROM roles AS r
JOIN (VALUES
    ('knowledge_base.read',      '{owner,admin,member,guest}'::text[]),
    ('knowledge_base.write',     '{owner,admin,member}'::text[]),
    ('knowledge_base.delete',    '{owner,admin,member}'::text[]),
    ('document.read',            '{owner,admin,member,guest}'::text[]),
    ('document.write',           '{owner,admin,member}'::text[]),
    ('document.delete',          '{owner,admin,member}'::text[]),
    ('chunk.read',               '{owner,admin,member,guest}'::text[]),
    ('job.read',                 '{owner,admin,member,guest}'::text[]),
    ('job.cancel',               '{owner,admin,member}'::text[]),
    ('query.run',                '{owner,admin,member,guest}'::text[]),
    ('chat.run',                 '{owner,admin,member,guest}'::text[]),
    ('lineage.read',             '{owner,admin,member,guest}'::text[]),
    ('index.rebuild',            '{owner,admin}'::text[]),
    ('workspace.manage_members', '{owner,admin}'::text[]),
    ('workspace.manage_roles',   '{owner,admin}'::text[])
) AS p(permission, roles)
  ON r.role_key = ANY (p.roles)
ON CONFLICT (role_id, permission) DO NOTHING;
