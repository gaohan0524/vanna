-- 评测/线上查询账号：只读，禁止 DDL/DML。
-- 用法（用管理员账号执行）：
--   clickhouse-client --multiquery < scripts/clickhouse/init_ro_user.sql
-- 执行前把 <password> 换成真实口令，并同步到 CH_PASSWORD 环境变量。

CREATE USER IF NOT EXISTS readonly
IDENTIFIED WITH sha256_password BY '<password>';

GRANT SELECT ON shop.* TO readonly;

-- 可选加固：readonly=1 会禁止修改其它 setting（包括 max_execution_time）；
-- 若要在评测里传查询级 settings，使用 readonly=2。
-- ALTER USER readonly SETTINGS readonly = 2;

-- 验证（必须失败）：
--   CREATE TABLE shop.__probe (x UInt8) ENGINE = Memory;
