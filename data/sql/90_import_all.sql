-- 全量导入入口：mysql -u <user> -p < data/sql/90_import_all.sql
-- 请将本文件与其余分片放在同一目录下执行

source 00_schema.sql;
source 50_platform_account.sql;
source 10_social_media.sql;
source 20_short_video.sql;
source 30_news_portal.sql;
source 40_industry_forum.sql;
