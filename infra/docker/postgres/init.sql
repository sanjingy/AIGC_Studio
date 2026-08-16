-- 初始化扩展。仅在首次创建数据卷时执行一次。
-- pgvector 用途见 17_ConsistencyEngine.md 第 7 节：存角色人脸 embedding。
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
