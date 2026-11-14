"""photos_lib 的单元测试:不需要 macOS、不碰照片 App(osascript 用假的代替,检查生成的脚本和调用顺序)。
    python3 -m unittest test_photos_lib -v
"""
import unittest
from pathlib import Path

import photos_lib as pl


class Fake:
    """假的 osa:按顺序返回预设结果,并记下每次收到的脚本。"""
    def __init__(self, *results):
        self.results, self.calls = list(results), []

    def __call__(self, script, timeout=60):
        self.calls.append(script)
        return self.results.pop(0) if self.results else (True, "ok")


class T(unittest.TestCase):
    def setUp(self):
        self._osa = pl.osa

    def tearDown(self):
        pl.osa = self._osa

    def test_names(self):
        self.assertEqual(pl.clean_person(" 张怡 "), "张怡")
        self.assertEqual(pl.clean_person(""), pl.NO_OWNER)
        self.assertEqual(pl.clean_person("a\"b" + chr(92) + "c" + chr(10)), "abc")
        self.assertEqual(pl.clean_day("2026-11-14"), "2026-11-14")
        self.assertRegex(pl.clean_day("乱七八糟"), r"^\d{4}-\d{2}-\d{2}$")   # 日期不对就用今天
        self.assertRegex(pl.clean_day(""), r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(pl._q("a\"b" + chr(92) + "c"), "a" + chr(92) + "\"b" + chr(92) + chr(92) + "c")

    def test_old_album_pattern(self):
        m = pl.OLD_RE.match("张怡 · 2026-11-14")
        self.assertEqual((m.group(1), m.group(2)), ("张怡", "2026-11-14"))
        self.assertEqual(pl.OLD_RE.match("管理员·2026-11-09").group(1), "管理员")
        self.assertIsNone(pl.OLD_RE.match("2026-11-14"))      # 新结构里的日期相册不会被当成旧的
        self.assertIsNone(pl.OLD_RE.match("我的收藏"))

    def test_import_into_album(self):
        pl.osa = Fake((True, "ok"), (True, "ok"))
        ok, msg = pl.import_pvt(Path("/tmp/IMG_L1_LIVE.pvt"), "张怡", "2026-11-14")
        self.assertTrue(ok)
        calls = pl.osa.calls
        self.assertEqual(len(calls), 2)
        self.assertIn('make new folder named "张怡"', calls[0])
        self.assertIn('make new album named "2026-11-14" at f', calls[0])
        self.assertIn('album "2026-11-14" of folder "张怡"', calls[1])
        self.assertIn('import POSIX file', calls[1])
        self.assertIn("into al", calls[1])

    def test_fallback_when_into_fails(self):
        pl.osa = Fake((True, "ok"), (False, "不支持 into"), (True, ""), (True, "ok"))
        pl.time.sleep = lambda s: None
        ok, msg = pl.import_pvt(Path("/tmp/IMG_L2_LIVE.pvt"), "", "2026-11-14")
        self.assertTrue(ok)
        self.assertIn("退路", msg)
        self.assertIn('folder "未署名"', pl.osa.calls[3])          # 没有发起人 → 未署名文件夹
        self.assertIn('filename starts with "IMG_L2_LIVE"', pl.osa.calls[3])

    def test_import_failures_reported(self):
        pl.osa = Fake((False, "照片 App 没响应"))
        ok, msg = pl.import_pvt(Path("/tmp/a.pvt"), "甲", "2026-11-14")
        self.assertFalse(ok)
        self.assertIn("建文件夹", msg)
        pl.osa = Fake((True, "ok"), (False, "x"), (False, "导入也失败"))
        pl.time.sleep = lambda s: None
        ok, msg = pl.import_pvt(Path("/tmp/a.pvt"), "甲", "2026-11-14")
        self.assertFalse(ok)
        self.assertIn("导入失败", msg)
        pl.osa = Fake((True, "ok"), (False, "x"), (True, ""), (True, "no-match"))
        ok, msg = pl.import_pvt(Path("/tmp/a.pvt"), "甲", "2026-11-14")
        self.assertFalse(ok)
        self.assertIn("最近项目", msg)

    def test_move_old_album(self):
        pl.osa = Fake((True, "0"), (True, ""))                  # 空相册:只删,不建新的
        self.assertEqual(pl.move_old_album("乙 · 2026-11-14", "乙", "2026-11-14"), (True, "ok"))
        self.assertEqual(len(pl.osa.calls), 2)
        self.assertIn('delete album "乙 · 2026-11-14"', pl.osa.calls[1])
        pl.osa = Fake((True, "5"), (True, "ok"), (True, "ok"), (True, ""))   # 有 5 张:建新相册 → 加进去 → 删旧的
        self.assertEqual(pl.move_old_album("丙 · 2026-11-13", "丙", "2026-11-13"), (True, "ok"))
        self.assertEqual(len(pl.osa.calls), 4)
        self.assertIn("add items_ to", pl.osa.calls[2])
        self.assertIn('delete album "丙 · 2026-11-13"', pl.osa.calls[3])
        pl.osa = Fake((True, "5"), (True, "ok"), (False, "加不进去"))         # 加失败:不删旧相册(照片不丢)
        ok, msg = pl.move_old_album("丁 · 2026-11-13", "丁", "2026-11-13")
        self.assertFalse(ok)
        self.assertEqual(len(pl.osa.calls), 3)

    def test_list_top_albums_parse(self):
        pl.osa = Fake((True, "张怡 · 2026-11-14\t12\n管理员 · 2026-11-14\t0\n我的收藏\t3\n"))
        self.assertEqual(pl.list_top_albums(), [{"name": "张怡 · 2026-11-14", "count": 12}, {"name": "管理员 · 2026-11-14", "count": 0}, {"name": "我的收藏", "count": 3}])


if __name__ == "__main__":
    unittest.main()
