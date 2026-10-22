-- 0009：页面级 AI 解读缓存（自我认知、关系）。
-- 0001–0008 已发布，不得修改。
--
-- 这两页没有「一条业务记录」可以挂结果，所以单独一张按用途存放的表；
-- 只存校验并渲染后的结果，原始出站载荷与模型原文不落库（第 7.4 节第 6 步）。

CREATE TABLE review_cache (
  purpose TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
