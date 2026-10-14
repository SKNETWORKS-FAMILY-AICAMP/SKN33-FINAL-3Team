"""공식 사전의 범위 연결과 원문 보류를 검증한다."""
import csv
import tempfile
import unittest
from pathlib import Path

import openpyxl

from backend.app.services.customs_reference import read_hsk, read_countries
from backend.app.services.customs_stage import Stage, ValidationError


class ReferenceTest(unittest.TestCase):
    def workbook(self, root, rows):
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = "2025년"
        sheet.append(["년도", "HS10단위부호", "세번2단위품명", "세번4단위품명", "세번6단위품명", "세번10단위품명"])
        for row in rows:
            sheet.append(row)
        path = root / "hsk.xlsx"
        book.save(path)
        book.close()
        return path

    def test_unsplit_scopes_and_full_original_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.workbook(Path(tmp), [
                ["2025", "0103100000", "동물", "돼지", "", "번식용"],
                ["2025", "8605001000", "철도", "객차와 특수차", "", "객차"],
                ["2025", "8605009000", "철도", "객차와 특수차", "", "기타"],
                ["2025", "0305541000", "어류", "건조 어류", "가" * 600, "제품"]])
            rows, report = read_hsk(path, 2025, "HS2022", "TEST FIXTURE")
            data = {row[0]: row for row in rows}
            self.assertEqual(data["010310"][3], "번식용")
            self.assertEqual(data["860500"][3], "객차와 특수차")
            self.assertLessEqual(len(data["030554"][2]), 120)
            self.assertEqual(data["0305"][2], "건조 어류")
            self.assertEqual(len(data["030554"][3]), 600)
            self.assertIsNone(data["0103100000"][3])
            self.assertEqual(report["unresolved_hs6"], [])

    def test_multiple_national_children_never_guess_hs6(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.workbook(Path(tmp), [
                ["2025", "0103911000", "동물", "돼지", "", "품목 A"],
                ["2025", "0103919000", "동물", "돼지", "", "품목 B"]])
            rows, report = read_hsk(path, 2025, "HS2022", "TEST FIXTURE")
            self.assertNotIn("010391", {row[0] for row in rows})
            self.assertEqual(report["unresolved_hs6"], ["010391"])

    def test_wrong_year_and_duplicate_codes_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            for rows in ([['2026', '0103100000', '동물', '돼지', '', '번식용']],
                         [['2025', '0103100000', '동물', '돼지', '', '번식용']] * 2):
                path = self.workbook(Path(tmp), rows)
                with self.assertRaises(ValidationError):
                    read_hsk(path, 2025, "HS2022", "TEST FIXTURE")

    def test_country_conflict_and_unknown_role_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "country.csv"
            path.write_text("year,statCd,statCdCntnKor1\n2025.01,NA,나미비아\n2025.02,NA,다른 이름\n", encoding="utf-8")
            with self.assertRaises(ValidationError):
                read_countries(path, {"NA"})
            with self.assertRaises(ValidationError):
                read_countries(path, set())


class HoldTest(unittest.TestCase):
    def prepare(self, root):
        path = root / "raw.csv"
        # 원문에 있는 추가 컬럼, 선행 0과 공백 중량도 보존해야 한다.
        path.write_text("year,statCd,hsCd,expDlr,expWgt,impDlr,impWgt,statKor\n"
            "2025.01,NA,0101210000,1,0,0,,원문 A\n"
            "2025.01,NA,0101290000,2,0,0,,원문 B\n", encoding="utf-8-sig")
        stage = Stage(root / "stage.db")
        stage.ingest(path, 2025, root / "errors.jsonl")
        return path, stage

    def test_only_unmapped_rows_held_original_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, stage = self.prepare(root)
            with stage:
                report = stage.hold_unmapped_hs(path, 2025, root / "held.csv", {"0101210000", "010121"})
                self.assertEqual(report["rows_held"], 1)
                self.assertEqual(stage.summary()[0]["export_usd"], "1.00")
                with (root / "held.csv").open(encoding="utf-8-sig", newline="") as stream:
                    held = list(csv.DictReader(stream))
                self.assertEqual(held[0]["hsCd"], "0101290000")
                self.assertEqual(held[0]["impWgt"], "")
                self.assertEqual(held[0]["statKor"], "원문 B")
                self.assertEqual(held[0]["_hold_record"], "2")
                # 사전 보완 후 보류 CSV 자체를 기존 적재 CLI로 재처리할 수 있다.
                with Stage(root / "held.db") as retry:
                    self.assertEqual(retry.ingest(root / "held.csv", 2025, root / "retry_errors.jsonl")["rows_valid"], 1)

    def test_missing_parent_holds_and_all_held_cannot_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, stage = self.prepare(root)
            with stage, self.assertRaises(ValidationError):
                stage.hold_unmapped_hs(path, 2025, root / "held.csv", {"0101210000", "0101290000"})
            with (root / "held.csv").open(encoding="utf-8-sig", newline="") as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 2)

    def test_changed_source_keeps_stage_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, stage = self.prepare(root)
            path.write_text(path.read_text(encoding="utf-8-sig").replace(",2,0,", ",9,0,"), encoding="utf-8-sig")
            with stage:
                with self.assertRaises(ValidationError):
                    stage.hold_unmapped_hs(path, 2025, root / "held.csv", {"0101210000", "010121"})
                self.assertEqual(stage.summary()[0]["rows"], 2)


if __name__ == "__main__":
    unittest.main()
