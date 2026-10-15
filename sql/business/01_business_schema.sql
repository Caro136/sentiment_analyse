-- 智能舆情分析平台 · 业务区表结构
--
-- 存放平台的配置与业务流转数据：账号权限、监测主题、信源通道、预警规则与告警、
-- 处置工单、研判报告、审计日志。
--
-- 导入：mysql -u root -p < sql/business/01_business_schema.sql
-- （依赖 sql/analysis/01_analysis_schema.sql 已建库；本文件亦包含建库语句，可独立执行）

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

CREATE DATABASE IF NOT EXISTS `sentiment_monitor`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
USE `sentiment_monitor`;

-- ----------------------------
-- 用户与权限（对应 PRD FR-01~FR-05）
-- ----------------------------
DROP TABLE IF EXISTS `sys_user`;
CREATE TABLE `sys_user` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `user_id` varchar(64) NOT NULL COMMENT '用户业务ID',
  `username` varchar(64) NOT NULL COMMENT '登录名',
  `password_hash` varchar(255) NOT NULL COMMENT '口令哈希（bcrypt/scrypt，不可逆）',
  `display_name` varchar(128) NULL DEFAULT NULL COMMENT '显示名称',
  `role` varchar(32) NOT NULL COMMENT '角色：analyst分析师/pr公关运营/admin管理员',
  `status` varchar(16) NOT NULL DEFAULT 'active' COMMENT '状态：active/disabled',
  `created_at` bigint NOT NULL COMMENT '创建时间戳',
  `last_login_at` bigint NULL DEFAULT NULL COMMENT '最后登录时间戳',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_sys_user_username`(`username`) USING BTREE,
  UNIQUE INDEX `uk_sys_user_user_id`(`user_id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '系统用户';

DROP TABLE IF EXISTS `audit_log`;
CREATE TABLE `audit_log` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `user_id` varchar(64) NULL DEFAULT NULL COMMENT '操作人',
  `action` varchar(64) NOT NULL COMMENT '动作：login/config_update/ticket_flow/report_export/risk_override',
  `target` varchar(255) NULL DEFAULT NULL COMMENT '操作对象',
  `before_value` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '变更前值',
  `after_value` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '变更后值',
  `ip` varchar(64) NULL DEFAULT NULL COMMENT '来源IP',
  `operated_at` bigint NOT NULL COMMENT '操作时间戳',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `ix_audit_log_user_time`(`user_id`, `operated_at`) USING BTREE,
  INDEX `ix_audit_log_action`(`action`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '操作审计日志';

-- ----------------------------
-- 监测配置（对应 PRD FR-06~FR-13）
-- ----------------------------
DROP TABLE IF EXISTS `topic`;
CREATE TABLE `topic` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `topic_id` varchar(64) NOT NULL COMMENT '主题业务ID',
  `name` varchar(128) NOT NULL COMMENT '主题名称',
  `description` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '主题描述',
  `source_keyword` varchar(128) NULL DEFAULT NULL COMMENT '对应原始数据 source_keyword 字段的取值',
  `owner_id` varchar(64) NOT NULL COMMENT '负责人用户ID',
  `status` varchar(16) NOT NULL DEFAULT 'enabled' COMMENT '状态：enabled/disabled',
  `start_at` bigint NULL DEFAULT NULL COMMENT '生效时间戳',
  `created_at` bigint NOT NULL COMMENT '创建时间戳',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_topic_topic_id`(`topic_id`) USING BTREE,
  INDEX `ix_topic_owner`(`owner_id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '监测主题';

DROP TABLE IF EXISTS `topic_keyword`;
CREATE TABLE `topic_keyword` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `topic_id` varchar(64) NOT NULL COMMENT '所属主题',
  `word` varchar(128) NOT NULL COMMENT '关键词',
  `group_type` varchar(16) NOT NULL COMMENT '分组：brand品牌词/event事件词/competitor竞品词/exclude排除词',
  `weight` decimal(4,2) NOT NULL DEFAULT 1.00 COMMENT '权重',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `ix_topic_keyword_topic`(`topic_id`, `group_type`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '主题词包';

DROP TABLE IF EXISTS `source_channel`;
CREATE TABLE `source_channel` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `channel_id` varchar(64) NOT NULL COMMENT '通道业务ID',
  `channel_type` varchar(32) NOT NULL COMMENT '信源类型：social_media/short_video/news_portal/industry_forum',
  `name` varchar(64) NOT NULL COMMENT '通道名称',
  `enabled` tinyint NOT NULL DEFAULT 1 COMMENT '是否启用',
  `poll_interval_min` int NOT NULL DEFAULT 15 COMMENT '采集轮询间隔（分钟）',
  `lookback_hours` int NOT NULL DEFAULT 24 COMMENT '回溯时间窗（小时）',
  `last_collected_at` bigint NULL DEFAULT NULL COMMENT '最近采集时间戳',
  `last_collected_count` int NULL DEFAULT NULL COMMENT '最近采集条数',
  `updated_at` bigint NOT NULL COMMENT '更新时间戳',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_source_channel_channel_id`(`channel_id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '信源通道';

DROP TABLE IF EXISTS `topic_channel`;
CREATE TABLE `topic_channel` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `topic_id` varchar(64) NOT NULL COMMENT '主题业务ID',
  `channel_id` varchar(64) NOT NULL COMMENT '通道业务ID',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_topic_channel`(`topic_id`, `channel_id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '主题与信源通道关联';

-- ----------------------------
-- 预警（对应 PRD FR-52~FR-56）
-- ----------------------------
DROP TABLE IF EXISTS `alert_rule`;
CREATE TABLE `alert_rule` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `rule_id` varchar(64) NOT NULL COMMENT '规则业务ID',
  `rule_type` varchar(32) NOT NULL COMMENT '类型：negative_surge负向激增/sensitive_hit敏感词/kol_voice高权重发声',
  `threshold` decimal(10,4) NULL DEFAULT NULL COMMENT '阈值（如负向增幅 0.5 表示 50%）',
  `sensitive_words` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '敏感词库（rule_type=sensitive_hit）',
  `level` varchar(2) NOT NULL COMMENT '触发等级 S/A/B/C',
  `enabled` tinyint NOT NULL DEFAULT 1 COMMENT '是否启用',
  `updated_at` bigint NOT NULL COMMENT '更新时间戳',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_alert_rule_rule_id`(`rule_id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '预警规则';

DROP TABLE IF EXISTS `alert_record`;
CREATE TABLE `alert_record` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `alert_id` varchar(64) NOT NULL COMMENT '告警业务ID',
  `rule_id` varchar(64) NULL DEFAULT NULL COMMENT '触发规则',
  `event_id` varchar(64) NULL DEFAULT NULL COMMENT '关联事件',
  `level` varchar(2) NOT NULL COMMENT '等级 S/A/B/C',
  `status` varchar(16) NOT NULL DEFAULT 'unread' COMMENT '状态：unread/read/claimed/closed',
  `triggered_at` bigint NOT NULL COMMENT '触发时间戳',
  `responded_at` bigint NULL DEFAULT NULL COMMENT '响应时间戳',
  `merged_count` int NOT NULL DEFAULT 1 COMMENT '同事件合并计数',
  `detail` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '触发明细（各因子取值 JSON）',
  `notify_status` varchar(16) NULL DEFAULT NULL COMMENT '通知投递状态：sent/failed/degraded',
  `notify_error` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '投递失败原因',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_alert_record_alert_id`(`alert_id`) USING BTREE,
  INDEX `ix_alert_record_status_time`(`status`, `triggered_at`) USING BTREE,
  INDEX `ix_alert_record_level`(`level`) USING BTREE,
  INDEX `ix_alert_record_event`(`event_id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '告警记录';

-- ----------------------------
-- 处置工单（对应 PRD FR-49~FR-51）
-- ----------------------------
DROP TABLE IF EXISTS `ticket`;
CREATE TABLE `ticket` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `ticket_id` varchar(64) NOT NULL COMMENT '工单业务ID',
  `event_id` varchar(64) NOT NULL COMMENT '关联事件',
  `alert_id` varchar(64) NULL DEFAULT NULL COMMENT '来源告警',
  `status` varchar(16) NOT NULL DEFAULT 'pending' COMMENT '状态：pending/tracking/verified/debunked/resolved/ignored',
  `assignee_id` varchar(64) NULL DEFAULT NULL COMMENT '承办人',
  `risk_level` varchar(2) NULL DEFAULT NULL COMMENT '当前风险等级',
  `response_draft` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '公关回应草案',
  `created_at` bigint NOT NULL COMMENT '创建时间戳',
  `closed_at` bigint NULL DEFAULT NULL COMMENT '关闭时间戳',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_ticket_ticket_id`(`ticket_id`) USING BTREE,
  INDEX `ix_ticket_status`(`status`) USING BTREE,
  INDEX `ix_ticket_event`(`event_id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '处置工单';

DROP TABLE IF EXISTS `ticket_log`;
CREATE TABLE `ticket_log` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `ticket_id` varchar(64) NOT NULL COMMENT '所属工单',
  `action` varchar(32) NOT NULL COMMENT '动作：create/claim/status_change/comment/draft_adopt',
  `from_status` varchar(16) NULL DEFAULT NULL COMMENT '原状态',
  `to_status` varchar(16) NULL DEFAULT NULL COMMENT '新状态',
  `operator_id` varchar(64) NOT NULL COMMENT '操作人',
  `note` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '批注',
  `operated_at` bigint NOT NULL COMMENT '操作时间戳',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `ix_ticket_log_ticket`(`ticket_id`, `operated_at`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '工单流转记录';

-- ----------------------------
-- 研判报告（对应 PRD FR-57~FR-63）
-- ----------------------------
DROP TABLE IF EXISTS `report`;
CREATE TABLE `report` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `report_id` varchar(64) NOT NULL COMMENT '报告业务ID',
  `event_id` varchar(64) NULL DEFAULT NULL COMMENT '关联事件（专项报告）',
  `topic_id` varchar(64) NULL DEFAULT NULL COMMENT '关联主题（周期简报）',
  `report_type` varchar(16) NOT NULL COMMENT '类型：event专项/periodic周期',
  `title` varchar(255) NOT NULL COMMENT '报告标题',
  `status` varchar(16) NOT NULL DEFAULT 'generating' COMMENT '状态：generating/generated/failed',
  `error_message` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '失败原因（供前端提示与重试）',
  `generated_at` bigint NULL DEFAULT NULL COMMENT '生成完成时间戳',
  `created_by` varchar(64) NULL DEFAULT NULL COMMENT '发起人',
  `created_at` bigint NOT NULL COMMENT '创建时间戳',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_report_report_id`(`report_id`) USING BTREE,
  INDEX `ix_report_topic_time`(`topic_id`, `generated_at`) USING BTREE,
  INDEX `ix_report_event`(`event_id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '研判报告';

DROP TABLE IF EXISTS `report_file`;
CREATE TABLE `report_file` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `file_id` varchar(64) NOT NULL COMMENT '文件业务ID',
  `report_id` varchar(64) NOT NULL COMMENT '所属报告',
  `file_type` varchar(16) NOT NULL COMMENT '类型：md/html',
  `file_path` varchar(512) NOT NULL COMMENT '产物文件路径',
  `content_md` mediumtext CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT 'Markdown 原文（供在线预览）',
  `size_bytes` bigint NULL DEFAULT NULL COMMENT '文件大小',
  `created_at` bigint NOT NULL COMMENT '创建时间戳',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_report_file_file_id`(`file_id`) USING BTREE,
  INDEX `ix_report_file_report`(`report_id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '报告产物文件';

-- ----------------------------
-- 任务运行状态（对应 PRD FR-72）
-- ----------------------------
DROP TABLE IF EXISTS `task_run`;
CREATE TABLE `task_run` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `task_id` varchar(64) NOT NULL COMMENT '任务业务ID',
  `task_type` varchar(32) NOT NULL COMMENT '类型：collect/analyze/research/report/alert',
  `status` varchar(16) NOT NULL DEFAULT 'running' COMMENT '状态：running/success/failed',
  `started_at` bigint NOT NULL COMMENT '开始时间戳',
  `finished_at` bigint NULL DEFAULT NULL COMMENT '结束时间戳',
  `duration_ms` bigint NULL DEFAULT NULL COMMENT '耗时（毫秒）',
  `input_summary` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '输入摘要',
  `error_message` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '错误摘要',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_task_run_task_id`(`task_id`) USING BTREE,
  INDEX `ix_task_run_type_time`(`task_type`, `started_at`) USING BTREE,
  INDEX `ix_task_run_status`(`status`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '任务运行状态';

SET FOREIGN_KEY_CHECKS = 1;
