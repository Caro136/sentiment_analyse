"""配置加载测试。

**回归重点：角色级模型配置必须能从 .env 正确读取。**

曾经用嵌套模型 + `env_nested_delimiter="_"`，而角色字段名本身含下划线
（`insight_engine`）——分隔符与字段名冲突导致切分歧义，
`INSIGHT_ENGINE_API_KEY` 无法确定该切成 `insight_engine.api_key` 还是
`insight.engine.api.key`。后果是整个 .env **静默解析失败**：
服务照常启动、体检全绿，但所有模型调用都报"未配置"。

这类故障的特征是「不报错、不崩溃，只是所有值都是空的」，
只有对着真实配置文件断言才能发现。
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from engines.contracts.config import Settings
from engines.contracts.roles import ROLE_INFOS


class RoleConfigLoadingTest(unittest.TestCase):
    """用一个临时 .env 驱动解析，不依赖开发者本机的实际配置。"""

    def _settings_with(self, content: str) -> Settings:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env_file = Path(tmp.name) / ".env"
        env_file.write_text(content, encoding="utf-8")
        return Settings(_env_file=str(env_file))

    def test_reads_simple_role(self) -> None:
        settings = self._settings_with(
            "ANALYTICS_API_KEY=k1\n"
            "ANALYTICS_MODEL_NAME=m1\n"
            "ANALYTICS_BASE_URL=https://example.com/v1\n")
        cfg = settings.llm_for_role("analytics")
        self.assertEqual(cfg.api_key, "k1")
        self.assertEqual(cfg.model_name, "m1")
        self.assertEqual(cfg.base_url, "https://example.com/v1")
        self.assertTrue(cfg.configured)

    def test_reads_role_whose_field_name_contains_underscore(self) -> None:
        """`insight_engine` 是当初的故障点——字段名含下划线。"""
        settings = self._settings_with(
            "INSIGHT_ENGINE_API_KEY=k2\nINSIGHT_ENGINE_MODEL_NAME=m2\n")
        cfg = settings.llm_for_role("insight")
        self.assertEqual(cfg.api_key, "k2")
        self.assertEqual(cfg.model_name, "m2")
        self.assertTrue(cfg.configured)

    def test_every_registered_role_is_readable(self) -> None:
        """角色前缀来自注册表——新增角色时若忘了在 Settings 补字段，这里会失败。"""
        content = ""
        for info in ROLE_INFOS.values():
            prefix = info.config_attr.upper()
            content += f"{prefix}_API_KEY=k_{info.config_attr}\n"
            content += f"{prefix}_MODEL_NAME=m_{info.config_attr}\n"
        settings = self._settings_with(content)

        for info in ROLE_INFOS.values():
            with self.subTest(role=info.key):
                cfg = settings.llm_for_role(info.key)
                self.assertEqual(cfg.api_key, f"k_{info.config_attr}")
                self.assertTrue(cfg.configured,
                                f"角色 {info.key} 未读到配置，字段前缀={info.config_attr}")

    def test_incomplete_config_is_not_configured(self) -> None:
        """只填了 key 没填 model，不算配置完成——否则调用时才失败。"""
        settings = self._settings_with("ANALYTICS_API_KEY=only_key\n")
        self.assertFalse(settings.llm_for_role("analytics").configured)

    def test_unconfigured_role_is_empty(self) -> None:
        settings = self._settings_with("ANALYTICS_API_KEY=k\nANALYTICS_MODEL_NAME=m\n")
        self.assertFalse(settings.llm_for_role("media").configured)

    def test_unknown_role_raises(self) -> None:
        with self.assertRaises(KeyError):
            Settings(_env_file=None).llm_for_role("not_a_role")

    def test_timeout_default(self) -> None:
        settings = self._settings_with("ANALYTICS_API_KEY=k\nANALYTICS_MODEL_NAME=m\n")
        self.assertEqual(settings.llm_for_role("analytics").timeout_seconds, 120.0)

    def test_optional_sampling_params(self) -> None:
        settings = self._settings_with(
            "ANALYTICS_API_KEY=k\nANALYTICS_MODEL_NAME=m\n"
            "ANALYTICS_TEMPERATURE=0.3\nANALYTICS_MAX_TOKENS=2048\n"
            "ANALYTICS_TIMEOUT_SECONDS=30\n")
        cfg = settings.llm_for_role("analytics")
        self.assertEqual(cfg.temperature, 0.3)
        self.assertEqual(cfg.max_tokens, 2048)
        self.assertEqual(cfg.timeout_seconds, 30.0)

    def test_unknown_keys_are_ignored(self) -> None:
        """.env 里可能残留已废弃的配置项，不该导致启动失败。"""
        settings = self._settings_with(
            "ANALYTICS_API_KEY=k\nANALYTICS_MODEL_NAME=m\nSOME_REMOVED_OPTION=x\n")
        self.assertTrue(settings.llm_for_role("analytics").configured)


class SettingsDerivationTest(unittest.TestCase):
    """派生属性与默认值。"""

    def test_database_url_encodes_credentials(self) -> None:
        """口令里的 @ : / 等字符若不编码，连接串会被截断。"""
        settings = Settings(_env_file=None)
        settings.db_user = "user"
        settings.db_password = "p@ss:word/1"
        url = settings.database_url
        self.assertIn("user", url)
        self.assertNotIn("p@ss:word/1", url)          # 原文不能直接出现
        self.assertIn("%40", url)                      # @ 已被编码

    def test_report_dirs_fall_back(self) -> None:
        settings = Settings(_env_file=None)
        self.assertTrue(settings.output_dir)
        self.assertTrue(settings.host_report_dir.endswith("host"))
        self.assertTrue(settings.insight_report_dir.endswith("insight"))

    def test_intensity_weights_present(self) -> None:
        weights = Settings(_env_file=None).intensity_weights
        self.assertEqual(set(weights), {"negative", "volume", "kol", "sensitive"})
        self.assertTrue(all(w >= 0 for w in weights.values()))

    def test_cors_origin_list_splits(self) -> None:
        settings = Settings(_env_file=None)
        settings.cors_origins = "http://a.com, http://b.com"
        self.assertEqual(settings.cors_origin_list, ["http://a.com", "http://b.com"])

    def test_startup_diagnostics_covers_every_role(self) -> None:
        diagnostics = Settings(_env_file=None).startup_diagnostics()
        names = [name for name, _, _ in diagnostics]
        for info in ROLE_INFOS.values():
            with self.subTest(role=info.key):
                self.assertTrue(any(info.display_name in n for n in names),
                                f"启动自检缺少角色 {info.key}")


if __name__ == "__main__":
    unittest.main()
