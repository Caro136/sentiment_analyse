-- 智能舆情分析平台 · 分析区表结构
--
-- 定位（设计规格 ADR-03）：
--   原始区（data/sql/ 交付的 9 张表）是采集产物，本项目**只读**；
--   分析结果一律写入本文件定义的表。这样模型或 Prompt 迭代时可以清空本区重跑，
--   不影响原始数据。
--
-- 关联方式：所有分析表通过 content_uid 关联原始内容，
--   格式 {source_type}:{source_table}:{business_id}
--   例如 short_video:douyin_aweme:7648947265968639247
--
-- 导入：mysql -u root -p < sql/analysis/01_analysis_schema.sql

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

CREATE DATABASE IF NOT EXISTS `sentiment_monitor`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
USE `sentiment_monitor`;

-- ----------------------------
-- 内容分析标签：认知分析的落库结果，是大盘/筛选/预警的公共数据源
-- ----------------------------
DROP TABLE IF EXISTS `content_analysis`;
CREATE TABLE `content_analysis` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `content_uid` varchar(255) NOT NULL COMMENT '跨信源统一标识 {source_type}:{source_table}:{business_id}',
  `source_type` varchar(32) NOT NULL COMMENT '信源类型：social_media/short_video/news_portal/industry_forum',
  `source_table` varchar(64) NOT NULL COMMENT '来源原始表名',
  `business_id` bigint NOT NULL COMMENT '原始表业务ID',
  `topic_id` varchar(64) NULL DEFAULT NULL COMMENT '归属监测主题',
  `sentiment` varchar(16) NOT NULL COMMENT '情感：positive/neutral/negative',
  `emotion` varchar(16) NULL DEFAULT NULL COMMENT '细分情绪：愤怒/失望/恐慌/质疑',
  `sentiment_confidence` decimal(5,4) NULL DEFAULT NULL COMMENT '情感置信度',
  `intensity_index` decimal(5,4) NULL DEFAULT NULL COMMENT '舆情烈度指数 0~1',
  `risk_level` varchar(2) NULL DEFAULT NULL COMMENT '风险等级：S/A/B/C',
  `risk_factors` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '风险判定的各因子取值（JSON）',
  `is_manual_override` tinyint NOT NULL DEFAULT 0 COMMENT '是否被人工覆盖',
  `override_reason` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '人工调整理由',
  `simhash` varchar(64) NULL DEFAULT NULL COMMENT 'SimHash 指纹（去重与刷量识别）',
  `is_suspected_bot` tinyint NOT NULL DEFAULT 0 COMMENT '疑似水军',
  `model_version` varchar(64) NULL DEFAULT NULL COMMENT '标签生成模型版本',
  `analyzed_at` bigint NOT NULL COMMENT '打标时间戳',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_content_analysis_uid`(`content_uid`) USING BTREE,
  INDEX `ix_content_analysis_filter`(`source_type`, `sentiment`, `risk_level`) USING BTREE,
  INDEX `ix_content_analysis_time`(`analyzed_at`) USING BTREE,
  INDEX `ix_content_analysis_topic`(`topic_id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '内容分析标签';

-- ----------------------------
-- 多模态解析产物：OCR / ASR 结果为独立证据记录，与原文并列而非替换原文
-- ----------------------------
DROP TABLE IF EXISTS `modality_asset`;
CREATE TABLE `modality_asset` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `asset_id` varchar(64) NOT NULL COMMENT '资产业务ID',
  `content_uid` varchar(255) NOT NULL COMMENT '关联内容统一标识',
  `asset_type` varchar(16) NOT NULL COMMENT '类型：image/video/audio',
  `origin_url` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '原始资源URL',
  `parsed_text` mediumtext CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT 'OCR 或 ASR 解析出的文本',
  `parse_status` varchar(16) NOT NULL DEFAULT 'pending' COMMENT '状态：pending/success/failed',
  `parser` varchar(32) NULL DEFAULT NULL COMMENT '解析器标识：ocr_mcp/asr_mock',
  `error_message` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '失败原因',
  `created_at` bigint NOT NULL COMMENT '创建时间戳',
  `parsed_at` bigint NULL DEFAULT NULL COMMENT '解析完成时间戳',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_modality_asset_asset_id`(`asset_id`) USING BTREE,
  INDEX `ix_modality_asset_content`(`content_uid`) USING BTREE,
  INDEX `ix_modality_asset_status`(`parse_status`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '多模态解析产物';

-- ----------------------------
-- 事件专题：同一事件的讨论聚合
-- ----------------------------
DROP TABLE IF EXISTS `event`;
CREATE TABLE `event` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `event_id` varchar(64) NOT NULL COMMENT '事件业务ID',
  `title` varchar(255) NOT NULL COMMENT '事件标题',
  `topic_id` varchar(64) NULL DEFAULT NULL COMMENT '归属主题',
  `risk_level` varchar(2) NULL DEFAULT NULL COMMENT '风险等级 S/A/B/C',
  `intensity_index` decimal(5,4) NULL DEFAULT NULL COMMENT '事件整体烈度指数',
  `first_seen_at` bigint NULL DEFAULT NULL COMMENT '首次出现时间戳',
  `last_seen_at` bigint NULL DEFAULT NULL COMMENT '最近出现时间戳',
  `total_volume` int NOT NULL DEFAULT 0 COMMENT '总声量',
  `negative_ratio` decimal(5,4) NULL DEFAULT NULL COMMENT '负面率',
  `status` varchar(16) NOT NULL DEFAULT 'monitoring' COMMENT '状态：monitoring/alerting/handling/resolved',
  `manual_override` tinyint NOT NULL DEFAULT 0 COMMENT '风险等级是否被人工复核覆盖（FR-24）',
  `override_reason` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '人工复核理由（manual_override=1 时必填）',
  `created_at` bigint NOT NULL COMMENT '创建时间戳',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_event_event_id`(`event_id`) USING BTREE,
  INDEX `ix_event_topic_status`(`topic_id`, `status`) USING BTREE,
  INDEX `ix_event_risk`(`risk_level`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '事件专题';

-- ----------------------------
-- 事件与内容的多对多关联
-- ----------------------------
DROP TABLE IF EXISTS `event_content`;
CREATE TABLE `event_content` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `event_id` varchar(64) NOT NULL COMMENT '事件业务ID',
  `content_uid` varchar(255) NOT NULL COMMENT '内容统一标识',
  `relevance` decimal(5,4) NULL DEFAULT NULL COMMENT '与事件的关联度',
  `added_at` bigint NOT NULL COMMENT '归入时间戳',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_event_content`(`event_id`, `content_uid`) USING BTREE,
  INDEX `ix_event_content_uid`(`content_uid`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '事件内容关联';

-- ----------------------------
-- 观点聚类：核心争议焦点与主流诉求
-- ----------------------------
DROP TABLE IF EXISTS `opinion_cluster`;
CREATE TABLE `opinion_cluster` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `cluster_id` varchar(64) NOT NULL COMMENT '聚类业务ID',
  `event_id` varchar(64) NULL DEFAULT NULL COMMENT '所属事件',
  `topic_id` varchar(64) NULL DEFAULT NULL COMMENT '所属主题',
  `label` varchar(128) NOT NULL COMMENT '簇标签（观点摘要短语）',
  `summary` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '簇摘要',
  `size` int NOT NULL DEFAULT 0 COMMENT '簇内成员数',
  `sentiment` varchar(16) NULL DEFAULT NULL COMMENT '簇主导情感',
  `intent` varchar(32) NULL DEFAULT NULL COMMENT '诉求意图：求解释/维权索赔/要求监管/情绪宣泄',
  `created_at` bigint NOT NULL COMMENT '创建时间戳',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_opinion_cluster_cluster_id`(`cluster_id`) USING BTREE,
  INDEX `ix_opinion_cluster_event`(`event_id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '观点聚类';

DROP TABLE IF EXISTS `cluster_member`;
CREATE TABLE `cluster_member` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `cluster_id` varchar(64) NOT NULL COMMENT '聚类业务ID',
  `content_uid` varchar(255) NOT NULL COMMENT '成员内容统一标识',
  `is_representative` tinyint NOT NULL DEFAULT 0 COMMENT '是否代表性言论',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `ix_cluster_member_cluster`(`cluster_id`) USING BTREE,
  INDEX `ix_cluster_member_uid`(`content_uid`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '观点簇成员';

-- ----------------------------
-- 传播溯源节点：首发、引爆、扩散等关键节点
-- ----------------------------
DROP TABLE IF EXISTS `spread_node`;
CREATE TABLE `spread_node` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `node_id` varchar(64) NOT NULL COMMENT '节点业务ID',
  `event_id` varchar(64) NOT NULL COMMENT '所属事件',
  `content_uid` varchar(255) NOT NULL COMMENT '对应内容统一标识',
  `node_role` varchar(16) NOT NULL COMMENT '节点角色：origin首发/ignition引爆/spread扩散/followup跟进/official回应',
  `account_weight` decimal(6,2) NULL DEFAULT NULL COMMENT '账号影响力评分',
  `occurred_at` bigint NOT NULL COMMENT '发生时间戳',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_spread_node_node_id`(`node_id`) USING BTREE,
  INDEX `ix_spread_node_event_time`(`event_id`, `occurred_at`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '传播溯源节点';

-- ----------------------------
-- 实体与实体关系（传播关联链路图谱）
-- ----------------------------
DROP TABLE IF EXISTS `entity`;
CREATE TABLE `entity` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `entity_id` varchar(64) NOT NULL COMMENT '实体业务ID',
  `name` varchar(128) NOT NULL COMMENT '实体名称',
  `entity_type` varchar(32) NOT NULL COMMENT '类型：机构/人物/产品/事件/地点',
  `created_at` bigint NOT NULL COMMENT '创建时间戳',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_entity_name_type`(`name`, `entity_type`) USING BTREE,
  INDEX `ix_entity_type`(`entity_type`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '舆情实体';

DROP TABLE IF EXISTS `entity_relation`;
CREATE TABLE `entity_relation` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `relation_id` varchar(64) NOT NULL COMMENT '关系业务ID',
  `event_id` varchar(64) NULL DEFAULT NULL COMMENT '所属事件',
  `from_entity_id` varchar(64) NOT NULL COMMENT '起始实体',
  `to_entity_id` varchar(64) NOT NULL COMMENT '目标实体',
  `relation_type` varchar(64) NOT NULL COMMENT '关系类型',
  `weight` decimal(6,4) NULL DEFAULT NULL COMMENT '关系权重（共现强度）',
  `created_at` bigint NOT NULL COMMENT '创建时间戳',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_entity_relation_id`(`relation_id`) USING BTREE,
  INDEX `ix_entity_relation_event`(`event_id`) USING BTREE,
  INDEX `ix_entity_relation_from`(`from_entity_id`) USING BTREE,
  INDEX `ix_entity_relation_to`(`to_entity_id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '实体关系';

SET FOREIGN_KEY_CHECKS = 1;
