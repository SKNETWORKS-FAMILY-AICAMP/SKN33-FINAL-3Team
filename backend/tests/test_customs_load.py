"""CSV 경계 조건과 전용 MySQL 테스트 DB의 공개 처리를 검증한다."""
import csv
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from backend.app.services.customs_stage import FIELDS, Stage, ValidationError, normalize
from backend.app.services.customs_load import Loader, TABLES
from backend.app.scheduler.jobs.customs_load import run
from argparse import Namespace


def row(**changes):
    return {"year": "2025.01", "statCd": "NA", "hsCd": "0101210000",
            "expDlr": "100.01", "expWgt": "0", "impDlr": "0", "impWgt": "", **changes}


def write_csv(path, rows):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


class CsvValidationTest(unittest.TestCase):
    def test_codes_zero_and_null_preserved(self):
        normalized = normalize(row(), 2025)
        self.assertEqual(normalized, ("2025-01-01", "NA", "0101210000", "100.01", "0.000", "0.00", None))

    def test_totals_excluded_but_malformed_totals_fail(self):
        self.assertIsNone(normalize(row(year="총계", statCd="-", hsCd="-"), 2025))
        with self.assertRaises(ValidationError):
            normalize(row(year="총계"), 2025)

    def test_bad_dates_codes_and_decimals_fail(self):
        changes = [dict(year="2024.01"), dict(year="2025.13"), dict(year="2025.1"),
                   dict(hsCd="101210000"), dict(statCd="-"), dict(expDlr=""),
                   dict(expDlr="-1"), dict(expDlr="NaN"), dict(expDlr="1e3"),
                   dict(expDlr="1.001"), dict(expWgt="0.0001"),
                   dict(expDlr="100000000000000000000"), dict(impWgt="-1")]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                normalize(row(**change), 2025)

    def test_unsorted_months_duplicates_errors_and_exact_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_csv(root / "raw.csv", [row(year="2025.02"), row(),
                row(hsCd="0101211000", expDlr="0.02"), row(), row(expDlr="-1"),
                row(year="총계", statCd="-", hsCd="-")])
            with Stage(root / "stage.db") as stage:
                counts = stage.ingest(root / "raw.csv", 2025, root / "errors.jsonl", batch_size=1)
                self.assertEqual(counts, {"rows_total": 6, "rows_valid": 3, "rows_total_line": 1,
                                         "invalid_rows": 1, "duplicate_keys": 1})
                result = stage.summary()
                self.assertEqual(result[0]["export_usd"], "100.03")
                self.assertEqual(result[0]["hs6_groups"], 1)
                self.assertEqual(stage.references(), ({"NA"}, {"0101210000", "0101211000", "010121"}))
                self.assertEqual(len(list(stage.rows("2025-01-01", 1))), 2)
            errors = [json.loads(line) for line in (root / "errors.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([error["record"] for error in errors], [4, 5])

    def test_large_amount_exact_aggregation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_csv(root / "raw.csv", [row(expDlr="99999999999999999999.99"),
                row(hsCd="0101211000", expDlr="99999999999999999999.99")])
            with Stage(root / "stage.db") as stage:
                stage.ingest(root / "raw.csv", 2025, root / "errors.jsonl")
                self.assertEqual(stage.summary()[0]["export_usd"], "199999999999999999999.98")

    def test_invalid_csv_never_calls_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_csv(root / "raw.csv", [row(expDlr="-1")])
            args = Namespace(csv=root / "raw.csv", year=2025, mode="load", hs_ver="HS2022",
                output=root / "out", batch_size=1, env=root / "missing.env", source_code="CUSTOMS")
            with patch("backend.app.scheduler.jobs.customs_load.connect_mysql") as connect:
                self.assertEqual(run(args), 1)
                connect.assert_not_called()
            report = json.loads(next((root / "out").glob("*/report.json")).read_text(encoding="utf-8"))
            self.assertEqual(report["error"]["error_type"], "ValidationError")
            self.assertFalse(report["database_loaded"])


@unittest.skipUnless(os.environ.get("CUSTOMS_TEST_DATABASE"), "전용 MySQL 테스트 DB 미설정")
class MySQLLoadTest(unittest.TestCase):
    """CUSTOMS_TEST_DATABASE는 customs_test_로 시작하는 일회용 전용 DB만 허용한다."""

    @classmethod
    def setUpClass(cls):
        import pymysql
        database = os.environ["CUSTOMS_TEST_DATABASE"]
        if not database.startswith("customs_test_") or not database.replace("_", "").isalnum():
            raise ValueError("CUSTOMS_TEST_DATABASE는 customs_test_ 접두사 필수")
        cls.connection = pymysql.connect(host=os.environ.get("CUSTOMS_TEST_HOST", "127.0.0.1"),
            port=int(os.environ.get("CUSTOMS_TEST_PORT", "3307")),
            user=os.environ.get("CUSTOMS_TEST_USER", "root"),
            password=os.environ.get("CUSTOMS_TEST_PASSWORD", ""), charset="utf8mb4", autocommit=False)
        with cls.connection.cursor() as cursor:
            # 전용 DB를 새로 생성한다. 이미 존재하는 DB는 초기화하지 않는다.
            cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
            cursor.execute(f"USE `{database}`")
            schema = Path(__file__).resolve().parents[2] / "db/mysql/init/01_schema.sql"
            for statement in schema.read_text(encoding="utf-8-sig").split(";"):
                if statement.strip():
                    cursor.execute(statement)
            cursor.execute("INSERT INTO service_option (option_code,option_type,label_ko,value_json) VALUES ('CUSTOMS','SOURCE','관세청','{}')")
            cursor.execute("INSERT INTO country (country_code,name_ko) VALUES ('NA','나미비아')")
            cursor.executemany("INSERT INTO hs_code (hs_code,hs_ver,name_ko,original_name_ko,source_edition) VALUES (%s,'HS2022',%s,%s,'TEST FIXTURE')", [
                ("010121", "테스트 HS6", "테스트 원본명"), ("0101210000", "테스트 HS10", None),
                ("0101211000", "테스트 HS10", None)])
        cls.connection.commit()

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()  # 검토할 수 있도록 테스트 DB를 남긴다. 자동 DROP하지 않는다.

    def setUp(self):
        self.events = []
        self.loader = Loader(self.connection, lambda event, **values: self.events.append((event, values)))
        with self.connection.cursor() as cursor:
            for table in (*TABLES, "batch_run"):
                cursor.execute(f"DELETE FROM {table}")
        self.connection.commit()

    def test_metrics_publish_without_changing_raw_amounts(self):
        from backend.app.services.customs_metrics import MetricCalculator
        self.load([row(year="2025.01",expDlr="100",expWgt="10",impDlr="10",impWgt="10"),
                   row(year="2025.02",expDlr="300",expWgt="10",impDlr="30",impWgt="10"),
                   row(year="2025.04",expDlr="1000",expWgt="10",impDlr="100",impWgt="10")])
        with self.connection.cursor() as cursor:
            cursor.execute("INSERT INTO service_option (option_code,option_type,label_ko,value_json) VALUES ('CUSTOMS_ANOMALY','RULE','테스트 규칙',%s)",
                           (json.dumps({"r2":{"hs_prefixes":["01"]}}),))
        self.connection.commit()
        result=MetricCalculator(self.connection).calculate(2025)
        self.assertEqual(result["rows"],{"export_monthly":3,"import_monthly":3})
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT amount_usd,history_months,baseline_amount_usd,median_unit_usd_kg,anomaly_code FROM export_monthly WHERE trade_month='2025-04-01'")
            self.assertEqual(cursor.fetchone(),(1000,2,200,20,"R2"))
            cursor.execute("SELECT baseline_amount_usd,median_unit_usd_kg FROM import_monthly WHERE trade_month='2025-04-01'")
            self.assertEqual(cursor.fetchone(),(20,2))
            cursor.execute("SELECT history_months,baseline_amount_usd,median_unit_usd_kg FROM export_monthly WHERE trade_month='2025-01-01'")
            self.assertEqual(cursor.fetchone(),(0,None,None))
            cursor.execute("SELECT COUNT(*) FROM batch_run WHERE job_name='CUSTOMS_METRICS' AND status='SUCCESS'")
            self.assertEqual(cursor.fetchone()[0],6)
            cursor.execute("DELETE FROM service_option WHERE option_code='CUSTOMS_ANOMALY'")
        self.connection.commit()

    def test_metrics_recalculate_existing_following_year(self):
        from backend.app.services.customs_metrics import MetricCalculator
        self.load([row(expDlr="300",expWgt="10")])
        with self.connection.cursor() as cursor:
            for table in ("export_monthly","import_monthly"):
                cursor.execute(f"INSERT INTO {table} (trade_month,country_code,hs10_code,hs_ver,amount_usd,weight_kg) VALUES ('2024-12-01','NA','0101210000','HS2022',100,10),('2026-01-01','NA','0101210000','HS2022',500,10)")
        self.connection.commit()
        MetricCalculator(self.connection).calculate(2025)
        self.assertEqual(self.query("SELECT history_months,baseline_amount_usd FROM export_monthly WHERE trade_month='2026-01-01'"),((1,300),))

    def test_metrics_roll_back_both_directions_on_second_update_failure(self):
        from backend.app.services.customs_metrics import MetricCalculator
        self.load([row()])
        class FailingCursor:
            def __init__(self,cursor): self.cursor=cursor
            def __getattr__(self,name): return getattr(self.cursor,name)
            def __enter__(self): self.cursor.__enter__(); return self
            def __exit__(self,*args): return self.cursor.__exit__(*args)
            def execute(self,sql,args=None):
                if sql.startswith("UPDATE import_monthly m JOIN customs_metrics_stage"):
                    raise ValidationError("수입 계산 공개 실패 모의")
                return self.cursor.execute(sql,args)
        class FailingConnection:
            def __init__(self,connection): self.connection=connection
            def __getattr__(self,name): return getattr(self.connection,name)
            def cursor(self): return FailingCursor(self.connection.cursor())
        with self.assertRaises(ValidationError): MetricCalculator(FailingConnection(self.connection)).calculate(2025)
        for table in ("export_monthly","import_monthly"):
            self.assertEqual(self.query(f"SELECT history_months FROM {table}"),((None,),))
        self.assertEqual(self.query("SELECT status,COUNT(*) FROM batch_run WHERE job_name='CUSTOMS_METRICS' GROUP BY status"),(("FAILED",2),))

    def load(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_csv(root / "raw.csv", rows)
            with Stage(root / "stage.db") as stage:
                counts = stage.ingest(root / "raw.csv", 2025, root / "errors.jsonl", 1)
                self.assertEqual(counts["invalid_rows"] + counts["duplicate_keys"], 0)
                return self.loader.load(stage, "HS2022", batch_size=1)

    def query(self, sql):
        with self.connection.cursor() as cursor:
            cursor.execute(sql)
            return cursor.fetchall()

    def test_repeat_and_correction_keep_omitted_rows_and_exact_summary(self):
        original = [row(), row(hsCd="0101211000", expDlr="0.02", impDlr="1.01")]
        self.load(original)
        self.load(original)
        self.assertEqual(self.query("SELECT COUNT(*) FROM export_monthly")[0][0], 2)
        self.assertEqual(str(self.query("SELECT amount_usd FROM export_summary")[0][0]), "100.03")
        self.assertEqual(str(self.query("SELECT amount_usd FROM import_summary")[0][0]), "1.01")
        self.assertEqual(self.query("SELECT DISTINCT status FROM batch_run"), (("SUCCESS",),))
        self.load([row(expDlr="200.00")])
        self.assertEqual(self.query("SELECT COUNT(*) FROM export_monthly")[0][0], 2)
        self.assertEqual(str(self.query("SELECT amount_usd FROM export_summary")[0][0]), "200.02")
        self.assertEqual(self.query("SELECT DISTINCT request_count FROM batch_run"), ((0,),))

    def test_missing_dictionary_rejected_before_publication(self):
        with self.assertRaises(ValidationError):
            self.load([row(hsCd="9999990000")])
        self.assertEqual(self.query("SELECT COUNT(*) FROM export_monthly")[0][0], 0)
        self.assertEqual(self.events[0][0], "reference_missing")

    def test_failure_rolls_back_both_directions_and_records_failed(self):
        self.load([row()])
        class FailingLoader(Loader):
            @classmethod
            def verify(cls, cursor, direction):
                if direction == "import":
                    raise ValidationError("의도적인 검증 실패")
                return super().verify(cursor, direction)
        self.loader = FailingLoader(self.connection, self.loader.log)
        with self.assertRaises(ValidationError):
            self.load([row(expDlr="777", impDlr="888")])
        self.assertEqual(str(self.query("SELECT amount_usd FROM export_monthly")[0][0]), "100.01")
        self.assertEqual(str(self.query("SELECT amount_usd FROM export_summary")[0][0]), "100.01")
        self.assertEqual(str(self.query("SELECT amount_usd FROM import_monthly")[0][0]), "0.00")
        self.assertEqual(self.query("SELECT COUNT(*) FROM batch_run WHERE status='FAILED'")[0][0], 4)

    def test_unchanged_values_keep_derived_fields_changed_history_invalidates(self):
        self.load([row(), row(year="2025.02"), row(year="2025.03")])
        with self.connection.cursor() as cursor:
            cursor.execute("UPDATE export_monthly SET history_months=3,baseline_amount_usd=1,median_unit_usd_kg=1,anomaly_code='R2'")
        self.connection.commit()
        self.load([row(year="2025.02")])
        self.assertEqual(self.query("SELECT DISTINCT anomaly_code FROM export_monthly"), (("R2",),))
        self.load([row(year="2025.02", expDlr="200")])
        self.assertEqual(self.query("SELECT DISTINCT anomaly_code FROM export_monthly"), ((None,),))

    def test_parallel_loader_lock_rejected(self):
        import hashlib
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE()")
            lock = "customs_load:" + hashlib.sha256(cursor.fetchone()[0].encode()).hexdigest()[:48]
            cursor.execute("SELECT GET_LOCK(%s,0)", (lock,))
        other = type(self.connection)(host=os.environ.get("CUSTOMS_TEST_HOST", "127.0.0.1"),
            port=int(os.environ.get("CUSTOMS_TEST_PORT", "3307")),
            user=os.environ.get("CUSTOMS_TEST_USER", "root"),
            password=os.environ.get("CUSTOMS_TEST_PASSWORD", ""), database=os.environ["CUSTOMS_TEST_DATABASE"])
        try:
            with tempfile.TemporaryDirectory() as tmp, Stage(Path(tmp) / "stage.db") as stage:
                root = Path(tmp)
                write_csv(root / "raw.csv", [row()])
                stage.ingest(root / "raw.csv", 2025, root / "errors.jsonl")
                with self.assertRaises(ValidationError):
                    Loader(other, self.loader.log).load(stage, "HS2022")
        finally:
            other.close()
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT RELEASE_LOCK(%s)", (lock,))

    def test_same_observation_in_another_version_rejected(self):
        with self.connection.cursor() as cursor:
            cursor.execute("INSERT INTO hs_code (hs_code,hs_ver,name_ko,original_name_ko,source_edition) VALUES ('010121','HS2017','테스트','테스트','TEST FIXTURE'),('0101210000','HS2017','테스트',NULL,'TEST FIXTURE')")
            cursor.execute("INSERT INTO export_monthly (trade_month,country_code,hs10_code,hs_ver,amount_usd) VALUES ('2025-01-01','NA','0101210000','HS2017',1)")
        self.connection.commit()
        with self.assertRaises(ValidationError):
            self.load([row()])
        self.assertEqual(self.query("SELECT hs_ver FROM export_monthly"), (("HS2017",),))
        self.assertEqual(self.query("SELECT COUNT(*) FROM import_monthly")[0][0], 0)

    def test_committed_month_survives_later_month_failure(self):
        class FebruaryFailure(Loader):
            @classmethod
            def verify(cls, cursor, direction):
                cursor.execute("SELECT MONTH(trade_month) FROM customs_stage LIMIT 1")
                if cursor.fetchone()[0] == 2:
                    raise ValidationError("2월 검증 실패")
                return super().verify(cursor, direction)
        self.loader = FebruaryFailure(self.connection, self.loader.log)
        with self.assertRaises(ValidationError):
            self.load([row(), row(year="2025.02")])
        self.assertEqual(self.query("SELECT trade_month FROM export_monthly")[0][0].isoformat(), "2025-01-01")
        self.assertEqual(self.query("SELECT COUNT(*) FROM export_monthly")[0][0], 1)
        self.assertEqual(self.query("SELECT status,COUNT(*) FROM batch_run GROUP BY status ORDER BY status"),
                         (("SUCCESS", 4), ("FAILED", 4)))

    def test_commit_response_loss_is_logged_without_marking_success_failed(self):
        class ResponseLostConnection:
            def __init__(self, connection):
                self.connection = connection
                self.publishing = False

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def begin(self):
                self.publishing = True
                self.connection.begin()

            def commit(self):
                self.connection.commit()
                if self.publishing:
                    raise RuntimeError("COMMIT 응답 손실 모의")

        self.loader = Loader(ResponseLostConnection(self.connection), self.loader.log)
        with self.assertRaises(RuntimeError):
            self.load([row()])
        self.assertEqual(self.query("SELECT COUNT(*) FROM export_monthly")[0][0], 1)
        self.assertEqual(self.query("SELECT DISTINCT status FROM batch_run"), (("SUCCESS",),))
        self.assertIn("commit_unknown", [event for event, _ in self.events])


if __name__ == "__main__":
    unittest.main()
