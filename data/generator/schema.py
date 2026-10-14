"""数据表契约定义。

本模块是整套模拟数据的唯一契约来源：DDL 与列顺序都在这里定义，
builder / emitter / eval_builder 都从这里取列顺序，避免各处手工维护列名导致错位。

设计原则（详见 docs/智能舆情分析平台-PRD.md 附录 A 与 data/README.md）：
1. 课程已有的 4 张表（weibo_* / douyin_*）的字段与命名习惯原样保留，
   包括两个平台之间刻意不一致的字段名（comment_count vs comments_count），
   这是接入层字段映射需求的真实来源，不要在此处做统一。
2. 新增的 4 张表沿用同样的命名与类型习惯：业务 ID 与主键分离、时间双写、
   互动量用 text 存储，使接入层只需一套映射规则。
3. 账号权重信息（粉丝量、认证类型）在课程表中缺失，统一补在 platform_account，
   供 PRD FR-29「账号权重评估」使用。
"""

DATABASE_NAME = "sentiment_monitor"

CREATE_DATABASE_SQL = (
    f"CREATE DATABASE IF NOT EXISTS `{DATABASE_NAME}` "
    "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;\n"
    f"USE `{DATABASE_NAME}`;\n"
)

# ---------------------------------------------------------------------------
# 表结构定义
# ---------------------------------------------------------------------------

# 信源分类，供自检与评测集标注使用
SOURCE_SOCIAL = "social_media"      # 社交媒体
SOURCE_VIDEO = "short_video"        # 短视频
SOURCE_NEWS = "news_portal"         # 新闻门户
SOURCE_FORUM = "industry_forum"     # 行业论坛

SOURCE_LABELS = {
    SOURCE_SOCIAL: "社交媒体",
    SOURCE_VIDEO: "短视频",
    SOURCE_NEWS: "新闻门户",
    SOURCE_FORUM: "行业论坛",
}


class TableSpec:
    """一张表的定义：列顺序、DDL、信源归属、表角色。"""

    def __init__(self, name, columns, ddl, source, kind):
        self.name = name
        self.columns = columns
        self.ddl = ddl
        self.source = source
        # kind 取值：account / content / comment / thread / post
        self.kind = kind

    @property
    def column_count(self):
        return len(self.columns)


TABLES = {}

# --- 1. 账号主数据（新增）----------------------------------------------------

TABLES["platform_account"] = TableSpec(
    name="platform_account",
    columns=[
        "id", "account_id", "platform", "nickname", "avatar",
        "follower_count", "verified_type", "influence_score",
        "ip_location", "is_suspected_bot", "add_ts",
    ],
    ddl="""
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
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '平台账号主数据'
""",
    source=None,
    kind="account",
)

# --- 2. 社交媒体（复用课程表，字段原样）---------------------------------------

TABLES["weibo_note"] = TableSpec(
    name="weibo_note",
    columns=[
        "id", "user_id", "nickname", "avatar", "gender", "profile_url",
        "ip_location", "add_ts", "last_modify_ts", "note_id", "content",
        "create_time", "create_date_time", "liked_count", "comments_count",
        "shared_count", "note_url", "source_keyword",
    ],
    ddl="""
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
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic
""",
    source=SOURCE_SOCIAL,
    kind="content",
)

TABLES["weibo_note_comment"] = TableSpec(
    name="weibo_note_comment",
    columns=[
        "id", "user_id", "nickname", "avatar", "gender", "profile_url",
        "ip_location", "add_ts", "last_modify_ts", "comment_id", "note_id",
        "content", "create_time", "create_date_time", "comment_like_count",
        "sub_comment_count", "parent_comment_id",
    ],
    ddl="""
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
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic
""",
    source=SOURCE_SOCIAL,
    kind="comment",
)

# --- 3. 短视频（复用课程表，字段原样）-----------------------------------------

TABLES["douyin_aweme"] = TableSpec(
    name="douyin_aweme",
    columns=[
        "id", "user_id", "sec_uid", "short_user_id", "user_unique_id",
        "nickname", "avatar", "user_signature", "ip_location", "add_ts",
        "last_modify_ts", "aweme_id", "aweme_type", "title", "desc",
        "create_time", "liked_count", "comment_count", "share_count",
        "collected_count", "aweme_url", "cover_url", "video_download_url",
        "music_download_url", "note_download_url", "source_keyword",
    ],
    ddl="""
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
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic
""",
    source=SOURCE_VIDEO,
    kind="content",
)

TABLES["douyin_aweme_comment"] = TableSpec(
    name="douyin_aweme_comment",
    columns=[
        "id", "user_id", "sec_uid", "short_user_id", "user_unique_id",
        "nickname", "avatar", "user_signature", "ip_location", "add_ts",
        "last_modify_ts", "comment_id", "aweme_id", "content", "create_time",
        "sub_comment_count", "parent_comment_id", "like_count", "pictures",
    ],
    ddl="""
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
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic
""",
    source=SOURCE_VIDEO,
    kind="comment",
)

# --- 4. 新闻门户（新增）------------------------------------------------------

TABLES["news_article"] = TableSpec(
    name="news_article",
    columns=[
        "id", "media_name", "author", "title", "content", "publish_time",
        "publish_date_time", "url", "read_count", "comment_count",
        "share_count", "source_keyword",
    ],
    ddl="""
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
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '新闻门户文章'
""",
    source=SOURCE_NEWS,
    kind="content",
)

TABLES["news_article_comment"] = TableSpec(
    name="news_article_comment",
    columns=[
        "id", "user_id", "nickname", "avatar", "ip_location", "comment_id",
        "article_id", "content", "create_time", "create_date_time",
        "comment_like_count", "sub_comment_count", "parent_comment_id",
    ],
    ddl="""
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
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '新闻门户评论'
""",
    source=SOURCE_NEWS,
    kind="comment",
)

# --- 5. 行业论坛（新增）------------------------------------------------------

TABLES["forum_thread"] = TableSpec(
    name="forum_thread",
    columns=[
        "id", "thread_id", "forum_name", "board", "author_id",
        "author_nickname", "title", "content", "publish_time",
        "publish_date_time", "view_count", "reply_count", "is_pinned",
        "is_essence", "url", "source_keyword",
    ],
    ddl="""
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
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '行业论坛主帖'
""",
    source=SOURCE_FORUM,
    kind="thread",
)

TABLES["forum_post"] = TableSpec(
    name="forum_post",
    columns=[
        "id", "post_id", "thread_id", "author_id", "author_nickname",
        "content", "create_time", "create_date_time", "like_count",
        "floor_no", "reply_to_post_id", "is_op",
    ],
    ddl="""
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
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic COMMENT = '行业论坛回帖'
""",
    source=SOURCE_FORUM,
    kind="post",
)

# 输出顺序：先账号，再按信源分组，保证导入时被引用表先于引用表存在
TABLE_ORDER = [
    "platform_account",
    "weibo_note", "weibo_note_comment",
    "douyin_aweme", "douyin_aweme_comment",
    "news_article", "news_article_comment",
    "forum_thread", "forum_post",
]

# 每个信源对应的内容表与评论表，供 builder 与自检使用
SOURCE_TABLES = {
    SOURCE_SOCIAL: {"content": "weibo_note", "comment": "weibo_note_comment"},
    SOURCE_VIDEO: {"content": "douyin_aweme", "comment": "douyin_aweme_comment"},
    SOURCE_NEWS: {"content": "news_article", "comment": "news_article_comment"},
    SOURCE_FORUM: {"content": "forum_thread", "comment": "forum_post"},
}


def build_schema_sql():
    """生成建库 + 建表 SQL（幂等，含 DROP）。"""
    lines = [
        "-- 由 data/generator/schema.py 生成，请勿手工编辑",
        "-- 智能舆情分析平台 · 模拟数据集表结构（共 %d 张表）" % len(TABLE_ORDER),
        "",
        "SET NAMES utf8mb4;",
        "SET FOREIGN_KEY_CHECKS = 0;",
        "",
        CREATE_DATABASE_SQL.rstrip(),
        "",
    ]
    for name in TABLE_ORDER:
        spec = TABLES[name]
        lines.append("-- ----------------------------")
        lines.append(f"-- Table structure for {name}")
        lines.append("-- ----------------------------")
        lines.append(f"DROP TABLE IF EXISTS `{name}`;")
        lines.append(spec.ddl.strip() + ";")
        lines.append("")
    lines.append("SET FOREIGN_KEY_CHECKS = 1;")
    lines.append("")
    return "\n".join(lines)
