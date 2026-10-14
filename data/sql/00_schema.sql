-- 由 data/generator/schema.py 生成，请勿手工编辑
-- 智能舆情分析平台 · 模拟数据集表结构（共 9 张表）

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

CREATE DATABASE IF NOT EXISTS `sentiment_monitor` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
USE `sentiment_monitor`;

-- ----------------------------
-- Table structure for platform_account
-- ----------------------------
DROP TABLE IF EXISTS `platform_account`;
CREATE TABLE `platform_account` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `account_id` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '账号业务ID',
  `platform` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '所属平台/信源',
  `nickname` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '昵称',
  `avatar` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '头像URL',
  `follower_count` bigint NULL DEFAULT 0 COMMENT '粉丝数',
  `verified_type` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT '普通用户' COMMENT '认证类型',
  `influence_score` decimal(6, 2) NULL DEFAULT 0.00 COMMENT '影响力评分0-100',
  `ip_location` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT 'IP归属地',
  `is_suspected_bot` tinyint NULL DEFAULT 0 COMMENT '是否疑似水军',
  `add_ts` bigint NULL DEFAULT NULL COMMENT '添加时间戳',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uk_platform_account_account_id`(`account_id`) USING BTREE,
  INDEX `ix_platform_account_platform`(`platform`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '平台账号主数据';

-- ----------------------------
-- Table structure for weibo_note
-- ----------------------------
DROP TABLE IF EXISTS `weibo_note`;
CREATE TABLE `weibo_note` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `user_id` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '用户ID',
  `nickname` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '用户昵称',
  `avatar` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '用户头像',
  `gender` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '性别',
  `profile_url` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '个人主页URL',
  `ip_location` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT 'IP地址位置',
  `add_ts` bigint NULL DEFAULT NULL COMMENT '添加时间戳',
  `last_modify_ts` bigint NULL DEFAULT NULL COMMENT '最后修改时间戳',
  `note_id` bigint NULL DEFAULT NULL COMMENT '笔记ID',
  `content` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '笔记内容',
  `create_time` bigint NULL DEFAULT NULL COMMENT '创建时间戳',
  `create_date_time` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '创建日期时间',
  `liked_count` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '点赞数',
  `comments_count` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '评论数',
  `shared_count` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '分享数',
  `note_url` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '笔记URL',
  `source_keyword` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '来源关键词',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `ix_weibo_note_create_time`(`create_time`) USING BTREE,
  INDEX `ix_weibo_note_note_id`(`note_id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for weibo_note_comment
-- ----------------------------
DROP TABLE IF EXISTS `weibo_note_comment`;
CREATE TABLE `weibo_note_comment` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `user_id` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '用户ID',
  `nickname` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '用户昵称',
  `avatar` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '用户头像',
  `gender` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '性别',
  `profile_url` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '个人主页URL',
  `ip_location` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT 'IP地址位置',
  `add_ts` bigint NULL DEFAULT NULL COMMENT '添加时间戳',
  `last_modify_ts` bigint NULL DEFAULT NULL COMMENT '最后修改时间戳',
  `comment_id` bigint NULL DEFAULT NULL COMMENT '评论ID',
  `note_id` bigint NULL DEFAULT NULL COMMENT '笔记ID',
  `content` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '评论内容',
  `create_time` bigint NULL DEFAULT NULL COMMENT '创建时间戳',
  `create_date_time` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '创建日期时间',
  `comment_like_count` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '评论点赞数',
  `sub_comment_count` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '子评论数',
  `parent_comment_id` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '父评论ID',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `ix_weibo_note_comment_note_id`(`note_id`) USING BTREE,
  INDEX `ix_weibo_note_comment_comment_id`(`comment_id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for douyin_aweme
-- ----------------------------
DROP TABLE IF EXISTS `douyin_aweme`;
CREATE TABLE `douyin_aweme` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `user_id` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '用户ID',
  `sec_uid` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '安全用户ID',
  `short_user_id` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '短用户ID',
  `user_unique_id` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '用户唯一ID',
  `nickname` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '用户昵称',
  `avatar` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '用户头像',
  `user_signature` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '用户签名',
  `ip_location` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT 'IP地址位置',
  `add_ts` bigint NULL DEFAULT NULL COMMENT '添加时间戳',
  `last_modify_ts` bigint NULL DEFAULT NULL COMMENT '最后修改时间戳',
  `aweme_id` bigint NULL DEFAULT NULL COMMENT '作品ID',
  `aweme_type` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '作品类型',
  `title` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '作品标题',
  `desc` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '作品描述',
  `create_time` bigint NULL DEFAULT NULL COMMENT '创建时间戳',
  `liked_count` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '点赞数',
  `comment_count` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '评论数',
  `share_count` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '分享数',
  `collected_count` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '收藏数',
  `aweme_url` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '作品URL',
  `cover_url` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '封面URL',
  `video_download_url` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '视频下载URL',
  `music_download_url` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '音乐下载URL',
  `note_download_url` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '笔记下载URL',
  `source_keyword` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '来源关键词',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `ix_douyin_aweme_create_time`(`create_time`) USING BTREE,
  INDEX `ix_douyin_aweme_aweme_id`(`aweme_id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for douyin_aweme_comment
-- ----------------------------
DROP TABLE IF EXISTS `douyin_aweme_comment`;
CREATE TABLE `douyin_aweme_comment` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `user_id` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '用户ID',
  `sec_uid` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '安全用户ID',
  `short_user_id` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '短用户ID',
  `user_unique_id` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '用户唯一ID',
  `nickname` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '用户昵称',
  `avatar` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '用户头像',
  `user_signature` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '用户签名',
  `ip_location` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT 'IP地址位置',
  `add_ts` bigint NULL DEFAULT NULL COMMENT '添加时间戳',
  `last_modify_ts` bigint NULL DEFAULT NULL COMMENT '最后修改时间戳',
  `comment_id` bigint NULL DEFAULT NULL COMMENT '评论ID',
  `aweme_id` bigint NULL DEFAULT NULL COMMENT '作品ID',
  `content` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '评论内容',
  `create_time` bigint NULL DEFAULT NULL COMMENT '创建时间戳',
  `sub_comment_count` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '子评论数',
  `parent_comment_id` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '父评论ID',
  `like_count` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '点赞数',
  `pictures` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '图片',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `ix_douyin_aweme_comment_aweme_id`(`aweme_id`) USING BTREE,
  INDEX `ix_douyin_aweme_comment_comment_id`(`comment_id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Table structure for news_article
-- ----------------------------
DROP TABLE IF EXISTS `news_article`;
CREATE TABLE `news_article` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `media_name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '媒体名称',
  `author` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '作者/记者',
  `title` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '文章标题',
  `content` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '文章正文',
  `publish_time` bigint NULL DEFAULT NULL COMMENT '发布时间戳',
  `publish_date_time` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '发布时间字符串',
  `url` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '文章URL',
  `read_count` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '阅读数',
  `comment_count` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '评论数',
  `share_count` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '转发数',
  `source_keyword` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '来源关键词',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `ix_news_article_publish_time`(`publish_time`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '新闻门户文章';

-- ----------------------------
-- Table structure for news_article_comment
-- ----------------------------
DROP TABLE IF EXISTS `news_article_comment`;
CREATE TABLE `news_article_comment` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `user_id` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '用户ID',
  `nickname` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '用户昵称',
  `avatar` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '用户头像',
  `ip_location` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT 'IP地址位置',
  `comment_id` bigint NULL DEFAULT NULL COMMENT '评论ID',
  `article_id` bigint NULL DEFAULT NULL COMMENT '文章ID',
  `content` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '评论内容',
  `create_time` bigint NULL DEFAULT NULL COMMENT '创建时间戳',
  `create_date_time` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '创建日期时间',
  `comment_like_count` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '评论点赞数',
  `sub_comment_count` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '子评论数',
  `parent_comment_id` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '父评论ID',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `ix_news_article_comment_article_id`(`article_id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '新闻门户评论';

-- ----------------------------
-- Table structure for forum_thread
-- ----------------------------
DROP TABLE IF EXISTS `forum_thread`;
CREATE TABLE `forum_thread` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `thread_id` bigint NULL DEFAULT NULL COMMENT '主帖ID',
  `forum_name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '论坛名称',
  `board` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '版块',
  `author_id` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '作者ID',
  `author_nickname` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '作者昵称',
  `title` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '帖子标题',
  `content` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '帖子正文',
  `publish_time` bigint NULL DEFAULT NULL COMMENT '发布时间戳',
  `publish_date_time` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '发布时间字符串',
  `view_count` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '浏览数',
  `reply_count` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '回复数',
  `is_pinned` tinyint NULL DEFAULT 0 COMMENT '是否置顶',
  `is_essence` tinyint NULL DEFAULT 0 COMMENT '是否精华',
  `url` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '帖子URL',
  `source_keyword` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '来源关键词',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `ix_forum_thread_publish_time`(`publish_time`) USING BTREE,
  INDEX `ix_forum_thread_thread_id`(`thread_id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '行业论坛主帖';

-- ----------------------------
-- Table structure for forum_post
-- ----------------------------
DROP TABLE IF EXISTS `forum_post`;
CREATE TABLE `forum_post` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `post_id` bigint NULL DEFAULT NULL COMMENT '回帖ID',
  `thread_id` bigint NULL DEFAULT NULL COMMENT '所属主帖ID',
  `author_id` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '作者ID',
  `author_nickname` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '作者昵称',
  `content` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '回帖内容',
  `create_time` bigint NULL DEFAULT NULL COMMENT '创建时间戳',
  `create_date_time` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '创建日期时间',
  `like_count` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL COMMENT '点赞数',
  `floor_no` int NULL DEFAULT NULL COMMENT '楼层号',
  `reply_to_post_id` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL DEFAULT NULL COMMENT '引用的回帖ID',
  `is_op` tinyint NULL DEFAULT 0 COMMENT '是否楼主本人回复',
  PRIMARY KEY (`id`) USING BTREE,
  INDEX `ix_forum_post_thread_id`(`thread_id`) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '行业论坛回帖';

SET FOREIGN_KEY_CHECKS = 1;
