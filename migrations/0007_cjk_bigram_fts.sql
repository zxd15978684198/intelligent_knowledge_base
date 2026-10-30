-- 0007_cjk_bigram_fts.sql
-- 目标：让 PostgreSQL 的全文检索对中文真正可用，且不引入任何新扩展。
--
-- 问题（实测，2026-10-29）：
--   to_tsvector('simple','银狐锚点报销标准')  ->  '银狐锚点报销标准':1
--   整段连续汉字被当成**一个词元**，于是
--   to_tsvector('simple','银狐锚点报销标准') @@ plainto_tsquery('simple','银狐')
--   的结果是 false —— 用户搜「银狐」永远搜不到任何中文文档。
--   这不是配置写错，而是 `simple` 分词器只按"非字母数字即分隔符"切分，
--   它对 ASCII 有效、对中文无效。
--
-- 为什么不用 pg_bigm / zhparser：
--   实测 `pg_available_extensions` 里没有这两个扩展，当前镜像
--   pgvector/pgvector:pg16 也未编译它们。要用就得自建镜像、所有成员和 CI 一起换，
--   代价远大于收益。可用的只有 pg_trgm（contrib 自带），但它是**模糊相似度**，
--   中文两字查询的三元组索引选择性很差，不适合当"精确检索"用。
--
-- 方案：中文重叠二元组（bigram）分词，纯 SQL 实现，复用现有 chunks.tsvector 列
--       与 chunks_tsvector_idx GIN 索引，零新扩展、零新镜像、零 Python 依赖。
--   连续汉字 run 长度 n -> n-1 个重叠二元组（银狐锚点 => 银狐 狐锚 锚点）；
--   ASCII 字母数字整词保留（PyKnora => pyknora；v2 => v2）；单字 run 保留原字。
--   这样"整段汉字一个词元"的问题消失，而检索仍走 GIN，性能不变。
--
-- 检索侧两级：先用 AND（要求命中全部二元组 ≈ 短语匹配），无结果再退到 OR
--   （任一命中，靠 ts_rank_cd 排序）。依据是实测：
--     '银狐报销标准'（不连续）对文档 '银狐锚点报销标准'：AND=f、OR=t
--     '一线城市住宿报销上限是多少钱'（长问句）：AND=f、OR=t
--   即 AND 保精度（专有名词/编号/错误码）、OR 保召回（自然语言问句）。
--
-- 已知边界（写在这里而不是留给下一个人踩）：
--   1. 单个汉字查询匹配不到含该字的多字词（'税' 找不到 '税率'）——
--      存储侧只产出二元组，不产出单字。最短有效查询长度为 2 个汉字。
--   2. 二元组会让 tsvector 变大（约 512 字的中文块约 330 个词元），
--      索引随之变大，这是为了让中文可检索付出的代价。
--   3. 本函数按 Unicode 码点判 CJK，不做词性/停用词处理；停用词靠 ts_rank_cd
--      自然降权，不做人工词表。

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------- 分词

-- 把文本切成"可索引词元"串（空格分隔），交给 to_tsvector('simple', ...) 使用。
-- STRICT：src 为 NULL 时返回 NULL，避免把 NULL 变成空串掩盖上游缺数据。
-- IMMUTABLE：只依赖入参，可被索引表达式/生成列使用，也让 PG 能缓存计划。
CREATE OR REPLACE FUNCTION pyknora_cjk_bigram(src text) RETURNS text
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE STRICT AS $fn$
DECLARE
    -- 汉字（基本区 4E00-9FFF / 扩展A 3400-4DBF / 兼容 F900-FAFF）
    -- + 日文假名（3040-30FF）+ 韩文音节（AC00-D7AF）视作同类"表意字 run"；
    -- ASCII 字母数字视为整词。其余字符（标点/空白/符号）一律当分隔符。
    pat CONSTANT text := '([一-鿿㐀-䶿豈-﫿ぁ-ヿ가-힣]+|[a-z0-9]+)';
    s   text := lower(src);
    tok text[] := ARRAY[]::text[];
    piece text;
    k   int;
BEGIN
    FOR piece IN SELECT m[1] FROM regexp_matches(s, pat, 'g') AS t(m) LOOP
        IF piece ~ '^[a-z0-9]+$' THEN
            tok := tok || piece;                    -- 拉丁词/数字：整体保留
        ELSIF char_length(piece) = 1 THEN
            tok := tok || piece;                    -- 单字：无 bigram 可切，保留原字
        ELSE
            FOR k IN 1..char_length(piece) - 1 LOOP -- 连续表意字：重叠二元组
                tok := tok || substr(piece, k, 2);
            END LOOP;
        END IF;
    END LOOP;
    RETURN array_to_string(tok, ' ');
END;
$fn$;

COMMENT ON FUNCTION pyknora_cjk_bigram(text) IS
    '中文重叠二元组分词：连续汉字切成两两重叠的二元组，ASCII 词整词保留。'
    '产物是空格分隔的词元串，配合 to_tsvector(''simple'', ...) 使用。';

-- ---------------------------------------------------------------- 查询串

-- 把用户查询转成 tsquery 文本。join_op='and' 要求全部命中（精度），
-- 其余值用 'or'（召回）。返回 NULL 而不是空串：to_tsquery('simple','') 会抛
-- 语法错误，而 to_tsquery('simple', NULL) 返回 NULL，@@ 得 NULL，自然 0 行。
--
-- 安全性：词元只可能是汉字/字母/数字，tsquery 的元字符（& | ! : * ( )）
-- 在分词阶段就被当分隔符丢掉了，因此不存在把用户输入当 tsquery 语法执行的问题。
CREATE OR REPLACE FUNCTION pyknora_cjk_tsquery(src text, join_op text DEFAULT 'or')
RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $fn$
    SELECT nullif(
        array_to_string(
            ARRAY(SELECT DISTINCT t
                  FROM unnest(string_to_array(pyknora_cjk_bigram(src), ' ')) AS t
                  WHERE t <> ''),
            CASE WHEN lower(join_op) = 'and' THEN ' & ' ELSE ' | ' END),
        '');
$fn$;

COMMENT ON FUNCTION pyknora_cjk_tsquery(text, text) IS
    '把中文查询转成 tsquery 文本（and=精确短语，or=宽松召回）；无可用词元时返回 NULL。';

-- ---------------------------------------------------------------- 回填

-- 已存在的 chunks 用的是旧口径（整段汉字一个词元），必须重算，否则本次修改
-- 只对新写入的数据生效、旧数据仍然搜不到。
--
-- IS DISTINCT FROM 让重跑变成幂等且几乎零成本：只有真正需要改的行才会被写。
-- 本机 chunks 当时是 0 行，回填耗时为零；有数据的库会是一次全表扫描 + 部分重写。
UPDATE chunks
   SET tsvector = to_tsvector('simple', pyknora_cjk_bigram(content))
 WHERE content IS NOT NULL
   AND tsvector IS DISTINCT FROM to_tsvector('simple', pyknora_cjk_bigram(content));

-- 函数与回填一起生效。tsvector 列与 chunks_tsvector_idx 索引均沿用 0001 的定义，
-- 本次不新增索引：binom 只是改变了词元内容，GIN 对 tsvector 的索引方式不变。
