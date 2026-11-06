-- 0015：会话令牌存储（只追加新号段，不改 0001–0014 任何文件）。
--
-- 为什么只存哈希：登录/注册时 256bit 随机刷新串的明文只通过 Set-Cookie 回一次，
--   库里留 sha256(token)。库被读走也换不出可用会话。
-- 为什么有 family_id：一次登录 = 一个家族；每次刷新插入后继行并沿用同一 family_id，
--   绝对过期时间原样复制（不滑动延长）。检测到「旧令牌重用」时整族一起标 revoked_at。
-- 为什么 replaced_by 不建自引用外键：清理按 expires_at 直接删行，指针外键会逼出删除排序。
-- 索引：family_idx 供整族撤销/查活跃后继；live_expiry_idx 是部分索引，只覆盖活跃行，
--   顺带清理与查活跃后继都命中它。
-- 幂等：CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS，重复执行无副作用。

\set ON_ERROR_STOP on

CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash    varchar(64) PRIMARY KEY,
    family_id     uuid          NOT NULL,
    principal_id  uuid          NOT NULL REFERENCES principals(principal_id) ON DELETE RESTRICT,
    csrf_hash     varchar(64)  NOT NULL,
    expires_at    timestamptz   NOT NULL,
    created_at    timestamptz   NOT NULL DEFAULT now(),
    rotated_at    timestamptz,
    replaced_by   varchar(64),
    revoked_at    timestamptz
);

CREATE INDEX IF NOT EXISTS refresh_tokens_family_idx
    ON refresh_tokens (family_id);

CREATE INDEX IF NOT EXISTS refresh_tokens_live_expiry_idx
    ON refresh_tokens (expires_at)
    WHERE revoked_at IS NULL AND replaced_by IS NULL;
