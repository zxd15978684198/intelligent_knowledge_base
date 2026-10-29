-- PyKnora 成员4：身份与权限基座（任务 9/10/11/12/15 的物理承载）
--
-- 运行方式（与 0001 一致，不要给容器传宿主路径）：
--   uv run python scripts/migrate.py
--
-- 三条设计取舍，改之前先读完：
--
--   1. 表名是 `principals` 而不是 `users`：全仓（models/tables.py、
--      document_versions.created_by_principal_id、JWT 的 sub）都按 principal 口径命名。
--      半路改回 users 会让"谁创建了这个文档"这条溯源链断在两张表之间。
--   2. `workspace_memberships.user_id` 这个名字**故意**保留（引用 principals.principal_id）：
--      它是"成员关系表里的用户列"，与 principals 主键同名反而会误导读者以为两者是同一列。
--   3. RLS 只加在**业务数据表**上，身份表不加：鉴权本身要跨工作区读成员关系
--      （"这个人在哪些空间里"），给 workspace_memberships 套 workspace 策略会把自己锁死。
--      身份表的隔离由应用层 SQL 谓词负责，RLS 只做业务数据的兜底。
--
-- 迁移纪律：已合并文件不改；以后改结构新建 0004_*.sql。

\set ON_ERROR_STOP on

-- ---------- 用户核心注册信息（任务9 唯一必需表） ----------
CREATE TABLE IF NOT EXISTS principals (
    principal_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    username       varchar(64)  NOT NULL UNIQUE,
    email          varchar(255) NOT NULL UNIQUE,
    -- 只存 bcrypt 哈希。列宽 255 足够放 `$2b$12$...`，不要改成 text 后又不做长度校验。
    password_hash  varchar(255) NOT NULL,
    display_name   varchar(128),
    status         varchar(16) NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active','disabled','deleted')),
    last_login_at  timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

-- ---------- 工作区（权限隔离根） ----------
-- documents / chunks 等表只有 workspace_id 而没有指向这里的复合外键：
-- 那些表归成员1/2 所有，本轮不越界改它们的 DDL；登录时创建的默认工作区
-- 只作为 Scope 的来源。跨表引用由各模块自己的复合外键（0001 已有）保证。
CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name               varchar(128) NOT NULL,
    owner_principal_id uuid NOT NULL REFERENCES principals(principal_id) ON DELETE RESTRICT,
    is_public          boolean NOT NULL DEFAULT false,
    created_at         timestamptz NOT NULL DEFAULT now()
);

-- ---------- 用户 ↔ 工作区 多对多（带属性） ----------
CREATE TABLE IF NOT EXISTS workspace_memberships (
    membership_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    user_id       uuid NOT NULL REFERENCES principals(principal_id) ON DELETE CASCADE,
    role          varchar(32) NOT NULL DEFAULT 'member'
                  CHECK (role IN ('owner','admin','member','guest')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    -- 同一人不许重复加入同一工作区：否则 _load_membership 的语义会变成
    -- "有任意一条就行"，而两条不同 role 的行谁生效就说不清了。
    UNIQUE (workspace_id, user_id)
);

CREATE INDEX IF NOT EXISTS workspace_memberships_user_idx
    ON workspace_memberships (user_id);

-- ---------- 角色定义 + 角色→权限点 ----------
CREATE TABLE IF NOT EXISTS roles (
    role_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    role_key    varchar(64) NOT NULL UNIQUE,
    description text
);

CREATE TABLE IF NOT EXISTS role_grants (
    grant_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    role_id    uuid NOT NULL REFERENCES roles(role_id) ON DELETE CASCADE,
    permission varchar(128) NOT NULL,
    UNIQUE (role_id, permission)
);

-- 只落一个默认角色。多角色树/权限编辑器属于后续需求，现在建空壳等于假装它已实现。
INSERT INTO roles (role_key, description)
VALUES ('member', '默认成员角色：只读访问本工作区的知识库与文档')
ON CONFLICT (role_key) DO NOTHING;

INSERT INTO role_grants (role_id, permission)
SELECT role_id, permission
FROM roles, (VALUES ('knowledge_base.read'), ('document.read'), ('chunk.read')) AS g(permission)
WHERE role_key = 'member'
ON CONFLICT (role_id, permission) DO NOTHING;

-- ---------- RLS 兜底：非 owner 应用角色 + 业务表 workspace 策略 ----------
-- 为什么必须是非 owner 角色：表 owner（postgres）天然 BYPASSRLS，用它测 RLS
-- 永远是"通过"，等于没测。pyknora_app 只有 DML 权限，没有 OWNER / SUPERUSER / BYPASSRLS。
--
-- 密码只用于本地开发（与 settings 里的 postgres:dev 同级）。生产应改为
-- 由部署系统注入，并在迁移里去掉 CREATE ROLE 这一段。
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pyknora_app') THEN
        CREATE ROLE pyknora_app LOGIN PASSWORD 'pyknora_app_dev';
    END IF;
END
$$;

DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO pyknora_app', current_database());
END
$$;

GRANT USAGE ON SCHEMA public TO pyknora_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO pyknora_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO pyknora_app;
-- 之后由成员1/5 新建的表（0002/0004）自动继承同一组权限，
-- 否则每加一张业务表就要手工补 GRANT，漏一次就是运行时 permission denied。
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO pyknora_app;

-- 策略统一为：行上的 workspace_id 必须等于当前事务里的 app.workspace_id。
-- 未设置时 current_setting(..., true) 返回 NULL → 比较结果为 NULL → 一行都读不到（fail-closed）。
-- 已存在的表（0001 的 8 张）与将来的知识库/引用表一并覆盖；to_regclass 负责跳过尚未建的表。
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'documents', 'document_versions', 'index_builds', 'chunks',
        'ingestion_jobs', 'job_attempts', 'document_blobs', 'outbox',
        'knowledge_bases', 'citations', 'index_migrations'
    ] LOOP
        IF to_regclass('public.' || t) IS NULL THEN
            CONTINUE;
        END IF;
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS ws_isolation ON %I', t);
        EXECUTE format($q$
            CREATE POLICY ws_isolation ON %I
                USING (workspace_id = current_setting('app.workspace_id', true)::uuid)
                WITH CHECK (workspace_id = current_setting('app.workspace_id', true)::uuid)
        $q$, t);
    END LOOP;
END
$$;

-- 视图默认按 owner 权限执行（security definer），非 owner 角色读视图会绕过底下各表的 RLS。
-- 打开 security_invoker 后，视图里的每一行都要过一遍策略，兜底才成立。
-- embedding_cache 没有 workspace_id 维度（内容哈希即全局缓存），不套策略。
DO $$
BEGIN
    IF to_regclass('public.v_active_chunks') IS NOT NULL THEN
        EXECUTE 'ALTER VIEW v_active_chunks SET (security_invoker = true)';
    END IF;
END
$$;
