"""多模态解析测试。

重点验证两条设计约束：
  1. **失败不阻断**（ADR-06）——OCR 挂了不该让内容入库停摆；
  2. **不硬编码工具签名**（ADR-07）——参数名从 schema 里选，不靠猜。

真实 OCR 调用需要可访问的图片地址，默认跳过（见文件末尾的 live 测试）。
"""

import asyncio
import os
import unittest

from engines.contracts.content import (
    ASSET_AUDIO,
    ASSET_IMAGE,
    ASSET_VIDEO,
    Author,
    MediaRef,
    UnifiedContent,
)
from engines.contracts.enums import SourceType
from engines.ingestion.adapters import get_adapter
from engines.ingestion.modality import (
    STATUS_FAILED,
    STATUS_SUCCESS,
    ModalityInput,
    ModalityOutput,
    ModalityParser,
    backfill_texts,
    build_asset_id,
    collect_inputs,
    get_parser,
    register_parser,
    reset_parsers,
    resolve_modality,
)
from engines.ingestion.modality.asr_mock import PLACEHOLDER_TEXT, MockAsrParser
from engines.ingestion.modality.ocr_mcp import OcrMcpParser, _pick_param_name

# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------

class FakeParser(ModalityParser):
    """可控的解析器，用于验证编排逻辑而不真正调用外部服务。"""

    parser_name = "fake"
    asset_type = ASSET_IMAGE

    def __init__(self, text: str = "识别出的文字", fail: bool = False,
                 raise_error: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.raise_error = raise_error
        self.calls: list[ModalityInput] = []

    async def parse(self, item: ModalityInput) -> ModalityOutput:
        self.calls.append(item)
        if self.raise_error:
            raise RuntimeError("解析器内部炸了")
        if self.fail:
            return self._failed(item, "模拟失败")
        return self._success(item, self.text)


class FakeOcrClient:
    """假的 MCP 客户端。"""

    def __init__(self, text: str = "图中文字", error: Exception | None = None,
                 configured: bool = True) -> None:
        self.text = text
        self.error = error
        self.configured = configured
        self.calls: list[str] = []

    async def recognize(self, image_url: str) -> str:
        self.calls.append(image_url)
        if self.error:
            raise self.error
        return self.text


def make_content(uid: str = "short_video:douyin_aweme:1", *,
                 media_refs: tuple[MediaRef, ...] = ()) -> UnifiedContent:
    return UnifiedContent(
        content_uid=uid,
        source_type=SourceType.VIDEO,
        source_table="douyin_aweme",
        business_id=1,
        author=Author(account_id="u1", nickname="测试"),
        text="视频描述文本",
        media_refs=media_refs,
    )


# ---------------------------------------------------------------------------
# 基础模型
# ---------------------------------------------------------------------------

class ModalityOutputTest(unittest.TestCase):
    def test_succeeded_flag(self) -> None:
        item = ModalityInput("a1", "uid", ASSET_IMAGE, "http://x/1.jpg")
        parser = MockAsrParser()
        self.assertTrue(parser._success(item, "文本").succeeded)
        self.assertFalse(parser._failed(item, "错误").succeeded)

    def test_has_text_ignores_whitespace(self) -> None:
        item = ModalityInput("a1", "uid", ASSET_IMAGE, "u")
        parser = MockAsrParser()
        self.assertFalse(parser._success(item, "   ").has_text)
        self.assertTrue(parser._success(item, "有内容").has_text)

    def test_failed_output_masks_secret(self) -> None:
        """错误信息会落库并展示，必须脱敏——OCR 异常里常带 Authorization 头。"""
        item = ModalityInput("a1", "uid", ASSET_IMAGE, "u")
        output = MockAsrParser()._failed(
            item, "调用失败 Authorization: Bearer sk-abcdefghijklmn")
        self.assertNotIn("abcdefghijklmn", output.error_message)

    def test_make_asset_id_stable(self) -> None:
        first = ModalityParser.make_asset_id("short_video", 123, "cover")
        second = ModalityParser.make_asset_id("short_video", 123, "cover")
        self.assertEqual(first, second)

    def test_build_asset_id_includes_asset_key(self) -> None:
        self.assertEqual(build_asset_id("short_video:douyin_aweme:1", "cover"),
                         "short_video:douyin_aweme:1#cover")


# ---------------------------------------------------------------------------
# 模拟 ASR
# ---------------------------------------------------------------------------

class MockAsrTest(unittest.TestCase):
    def _item(self, asset_type: str = ASSET_VIDEO) -> ModalityInput:
        return ModalityInput("a1", "uid1", asset_type, "http://x/v.mp4")

    def test_returns_placeholder_by_default(self) -> None:
        output = asyncio.run(MockAsrParser().parse(self._item()))
        self.assertTrue(output.succeeded)
        self.assertIn("模拟", output.parsed_text)

    def test_placeholder_is_marked_as_mock(self) -> None:
        """占位文本必须自曝是模拟数据，避免演示时被误认为真实转录。"""
        self.assertIn("模拟", PLACEHOLDER_TEXT)

    def test_uses_registered_sample(self) -> None:
        parser = MockAsrParser({"uid1": "这家店后厨真的太脏了"})
        output = asyncio.run(parser.parse(self._item()))
        self.assertEqual(output.parsed_text, "这家店后厨真的太脏了")

    def test_video_and_audio_both_supported(self) -> None:
        parser = MockAsrParser()
        self.assertTrue(asyncio.run(parser.parse(self._item(ASSET_VIDEO))).succeeded)
        self.assertTrue(asyncio.run(parser.parse(self._item(ASSET_AUDIO))).succeeded)

    def test_rejects_image(self) -> None:
        output = asyncio.run(MockAsrParser().parse(self._item(ASSET_IMAGE)))
        self.assertEqual(output.parse_status, STATUS_FAILED)


# ---------------------------------------------------------------------------
# OCR 参数名选择（不硬编码工具签名）
# ---------------------------------------------------------------------------

class OcrParamNameTest(unittest.TestCase):
    def test_picks_image_url(self) -> None:
        schema = {"properties": {"image_url": {"type": "string"}}}
        self.assertEqual(_pick_param_name(schema), "image_url")

    def test_picks_alternative_name(self) -> None:
        schema = {"properties": {"image": {"type": "string"}}}
        self.assertEqual(_pick_param_name(schema), "image")

    def test_picks_sole_param_when_unrecognized(self) -> None:
        """只有一个参数时就用它——总比猜一个不存在的名字好。"""
        schema = {"properties": {"picture_base64": {"type": "string"}}}
        self.assertEqual(_pick_param_name(schema), "picture_base64")

    def test_favors_known_name_over_sole_param(self) -> None:
        schema = {"properties": {"image_url": {}, "extra_flag": {}}}
        self.assertEqual(_pick_param_name(schema), "image_url")

    def test_fallback_on_empty_schema(self) -> None:
        self.assertEqual(_pick_param_name({}), "image_url")


class OcrParserTest(unittest.TestCase):
    def _item(self, url: str = "http://x/1.jpg",
              asset_type: str = ASSET_IMAGE) -> ModalityInput:
        return ModalityInput("a1", "uid1", asset_type, url)

    def test_unconfigured_client_returns_failed(self) -> None:
        """未配置密钥时返回 failed 而不是抛异常——失败必须放行。"""
        parser = OcrMcpParser(FakeOcrClient(configured=False))  # type: ignore[arg-type]
        output = asyncio.run(parser.parse(self._item()))
        self.assertEqual(output.parse_status, STATUS_FAILED)
        self.assertIn("未配置", output.error_message)

    def test_missing_url_returns_failed(self) -> None:
        parser = OcrMcpParser(FakeOcrClient())     # type: ignore[arg-type]
        self.assertEqual(asyncio.run(parser.parse(self._item(url=""))).parse_status,
                         STATUS_FAILED)

    def test_wrong_asset_type_returns_failed(self) -> None:
        parser = OcrMcpParser(FakeOcrClient())     # type: ignore[arg-type]
        output = asyncio.run(parser.parse(self._item(asset_type=ASSET_VIDEO)))
        self.assertEqual(output.parse_status, STATUS_FAILED)

    def test_success_returns_text(self) -> None:
        parser = OcrMcpParser(FakeOcrClient("现场照片中的标语"))  # type: ignore[arg-type]
        output = asyncio.run(parser.parse(self._item()))
        self.assertTrue(output.succeeded)
        self.assertEqual(output.parsed_text, "现场照片中的标语")

    def test_client_exception_becomes_failed_not_raised(self) -> None:
        """**核心约束**：客户端抛异常时解析器必须吞掉并转成 failed。"""
        parser = OcrMcpParser(FakeOcrClient(error=TimeoutError("连接超时")))  # type: ignore[arg-type]
        output = asyncio.run(parser.parse(self._item()))    # 不应抛异常
        self.assertEqual(output.parse_status, STATUS_FAILED)
        self.assertIn("TimeoutError", output.error_message)

    def test_empty_text_treated_as_failure(self) -> None:
        parser = OcrMcpParser(FakeOcrClient("   "))  # type: ignore[arg-type]
        self.assertEqual(asyncio.run(parser.parse(self._item())).parse_status,
                         STATUS_FAILED)


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

class RegistryTest(unittest.TestCase):
    def tearDown(self) -> None:
        reset_parsers()

    def test_default_parsers_available(self) -> None:
        self.assertEqual(get_parser(ASSET_IMAGE).parser_name, "ocr_mcp")
        self.assertEqual(get_parser(ASSET_VIDEO).parser_name, "asr_mock")
        self.assertEqual(get_parser(ASSET_AUDIO).parser_name, "asr_mock")

    def test_parser_is_singleton(self) -> None:
        self.assertIs(get_parser(ASSET_IMAGE), get_parser(ASSET_IMAGE))

    def test_unknown_type_raises(self) -> None:
        with self.assertRaises(KeyError):
            get_parser("hologram")

    def test_register_replaces_parser(self) -> None:
        fake = FakeParser()
        register_parser(ASSET_IMAGE, fake)
        self.assertIs(get_parser(ASSET_IMAGE), fake)


# ---------------------------------------------------------------------------
# 批量解析服务
# ---------------------------------------------------------------------------

class ServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeParser("图中文字：售后服务电话")
        register_parser(ASSET_IMAGE, self.fake)

    def tearDown(self) -> None:
        reset_parsers()

    def test_collect_inputs_expands_media_refs(self) -> None:
        content = make_content(media_refs=(
            MediaRef(ASSET_IMAGE, "http://x/a.jpg", "cover"),
            MediaRef(ASSET_VIDEO, "http://x/a.mp4", "video"),
        ))
        items = collect_inputs([content])
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].asset_id, f"{content.content_uid}#cover")
        self.assertEqual(items[1].asset_id, f"{content.content_uid}#video")

    def test_collect_inputs_without_media(self) -> None:
        self.assertEqual(collect_inputs([make_content()]), [])

    def test_resolve_backfills_text(self) -> None:
        content = make_content(media_refs=(
            MediaRef(ASSET_IMAGE, "http://x/a.jpg", "cover"),))
        result = asyncio.run(resolve_modality([content]))
        self.assertEqual(len(result.contents), 1)
        self.assertEqual(result.contents[0].modality_texts,
                         ("图中文字：售后服务电话",))
        self.assertEqual(result.succeeded, 1)

    def test_backfilled_text_joins_analysis_text(self) -> None:
        """OCR 文本要参与分析——图片里的文字往往正是关键信息。"""
        content = make_content(media_refs=(
            MediaRef(ASSET_IMAGE, "http://x/a.jpg", "cover"),))
        resolved = asyncio.run(resolve_modality([content])).contents[0]
        self.assertIn("售后服务电话", resolved.analysis_text)

    def test_content_without_media_unchanged(self) -> None:
        content = make_content()
        result = asyncio.run(resolve_modality([content]))
        self.assertEqual(result.contents[0].modality_texts, ())
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.total, 0)

    def test_original_content_not_mutated(self) -> None:
        """UnifiedContent 是 frozen，回填必须构造新实例。"""
        content = make_content(media_refs=(
            MediaRef(ASSET_IMAGE, "http://x/a.jpg", "cover"),))
        asyncio.run(resolve_modality([content]))
        self.assertEqual(content.modality_texts, ())

    def test_one_failure_does_not_block_others(self) -> None:
        """**核心约束**：一张图识别失败，其余照常解析。"""
        good = make_content("short_video:douyin_aweme:1", media_refs=(
            MediaRef(ASSET_IMAGE, "http://x/good.jpg", "cover"),))
        bad = make_content("short_video:douyin_aweme:2", media_refs=(
            MediaRef(ASSET_IMAGE, "http://x/bad.jpg", "cover"),))

        call_count = {"n": 0}

        class FlakyParser(FakeParser):
            async def parse(self, item):
                call_count["n"] += 1
                if "bad" in item.origin_url:
                    return self._failed(item, "链接不可访问")
                return self._success(item, "成功识别的文字")

        register_parser(ASSET_IMAGE, FlakyParser())
        result = asyncio.run(resolve_modality([good, bad]))

        self.assertEqual(result.total, 2)
        self.assertEqual(result.succeeded, 1)
        self.assertEqual(result.failed, 1)
        # 成功的那个仍然拿到了文本
        by_uid = {c.content_uid: c for c in result.contents}
        self.assertEqual(by_uid["short_video:douyin_aweme:1"].modality_texts,
                         ("成功识别的文字",))
        self.assertEqual(by_uid["short_video:douyin_aweme:2"].modality_texts, ())

    def test_parser_raising_does_not_break_batch(self) -> None:
        """解析器理论上不该抛异常，但真抛了也不能中断整批。"""
        register_parser(ASSET_IMAGE, FakeParser(raise_error=True))
        content = make_content(media_refs=(
            MediaRef(ASSET_IMAGE, "http://x/a.jpg", "cover"),))
        result = asyncio.run(resolve_modality([content]))    # 不应抛异常
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.contents[0].modality_texts, ())

    def test_backfill_with_multiple_assets(self) -> None:
        outputs = [
            ModalityOutput("a1", "uid1", ASSET_IMAGE, "p", STATUS_SUCCESS,
                           parsed_text="第一段"),
            ModalityOutput("a2", "uid1", ASSET_VIDEO, "p", STATUS_SUCCESS,
                           parsed_text="第二段"),
            ModalityOutput("a3", "uid2", ASSET_IMAGE, "p", STATUS_FAILED),
        ]
        contents = [make_content("uid1"), make_content("uid2")]
        updated = backfill_texts(contents, outputs)
        self.assertEqual(updated[0].modality_texts, ("第一段", "第二段"))
        self.assertEqual(updated[1].modality_texts, ())


# ---------------------------------------------------------------------------
# 适配器产出的媒体引用
# ---------------------------------------------------------------------------

class MediaRefExtractionTest(unittest.TestCase):
    """媒体地址在多模态解析链路上是必需的输入，映射时不能丢。"""

    def test_douyin_yields_cover_and_video(self) -> None:
        row = {
            "aweme_id": 1, "desc": "视频描述", "create_time": 100,
            "cover_url": "http://cdn/cover.jpg",
            "video_download_url": "http://cdn/v.mp4",
        }
        content = get_adapter(SourceType.VIDEO).to_content(row)
        types = {ref.asset_type for ref in content.media_refs}
        self.assertEqual(types, {ASSET_IMAGE, ASSET_VIDEO})

    def test_douyin_without_media(self) -> None:
        row = {"aweme_id": 1, "desc": "文本", "create_time": 100}
        self.assertEqual(get_adapter(SourceType.VIDEO).to_content(row).media_refs, ())

    def test_other_sources_have_no_content_media(self) -> None:
        """微博/新闻/论坛的原始表没有内容级媒体地址。"""
        weibo = {"note_id": 1, "content": "正文", "create_time": 100}
        news = {"id": 1, "title": "标题", "content": "正文", "publish_time": 100}
        forum = {"thread_id": 1, "title": "标题", "content": "正文",
                 "publish_time": 100}
        for adapter, row in ((get_adapter(SourceType.SOCIAL), weibo),
                             (get_adapter(SourceType.NEWS), news),
                             (get_adapter(SourceType.FORUM), forum)):
            with self.subTest(table=adapter.content_table):
                self.assertEqual(adapter.to_content(row).media_refs, ())

    def test_media_ref_asset_key_defaults_to_type(self) -> None:
        self.assertEqual(MediaRef(ASSET_IMAGE, "u").asset_key, ASSET_IMAGE)
        self.assertEqual(MediaRef(ASSET_IMAGE, "u", "cover").asset_key, "cover")


# ---------------------------------------------------------------------------
# 真实 OCR 调用（默认跳过）
# ---------------------------------------------------------------------------

@unittest.skipUnless(os.environ.get("RUN_OCR_LIVE"),
                     "需要显式开启：set RUN_OCR_LIVE=1 并指定 OCR_LIVE_IMAGE_URL")
class LiveOcrTest(unittest.TestCase):
    """对着真实 MCP 服务跑一次，验证工具发现与参数选择是否正确。

    这条测试无法自动化——它需要一张公网可访问的图片。但它值得保留：
    真实的工具名与参数名只有连上才知道，第一次联调必须实际跑一次。
    """

    def test_live_ocr_call(self) -> None:
        from engines.ingestion.modality.ocr_mcp import default_client

        image_url = os.environ["OCR_LIVE_IMAGE_URL"]
        text = asyncio.run(default_client().recognize(image_url))
        print(f"\n[Live OCR] 工具={default_client().resolved_tool} 识别结果={text[:100]}")
        self.assertIsInstance(text, str)


if __name__ == "__main__":
    unittest.main()
