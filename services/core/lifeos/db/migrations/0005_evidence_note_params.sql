-- 0005：证据说明的模板参数（第 16.6 节 / NFR-08）。
-- 0004 已发布，不得修改；这里只新增一列。
--
-- hypothesis_evidence.note 有两种语义：规则生成的 i18n 键（added_by='rule'）
-- 和用户补充的自由文本（added_by='user'）。前者的模板参数（如 {status}）需要
-- 跟着一起存，读库时才能还原成 {key, params} 消息对象交给渲染层本地化。

ALTER TABLE hypothesis_evidence ADD COLUMN note_params TEXT;
