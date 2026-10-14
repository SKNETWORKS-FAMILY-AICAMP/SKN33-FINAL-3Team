"""검증된 임시 데이터를 월별 트랜잭션으로 MySQL에 공개한다."""
import calendar
import hashlib
import re
from collections import defaultdict
from datetime import date
from time import perf_counter

from backend.app.services.customs_stage import ValidationError
from backend.app.db.mysql import ConfigurationError

TABLES = ("export_monthly", "import_monthly", "export_summary", "import_summary")
STAGE_DDL = """CREATE TEMPORARY TABLE customs_stage (
    trade_month DATE NOT NULL,
    country_code VARCHAR(12) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    hs10_code VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    hs6_code VARCHAR(6) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    hs_ver VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    export_amount DECIMAL(22,2) NOT NULL, export_weight DECIMAL(22,3) NULL,
    import_amount DECIMAL(22,2) NOT NULL, import_weight DECIMAL(22,3) NULL,
    export_changed BOOLEAN NOT NULL DEFAULT TRUE,
    import_changed BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (trade_month,country_code,hs10_code,hs_ver),
    KEY ix_stage_group (trade_month,country_code,hs6_code,hs_ver)
) ENGINE=InnoDB"""
STAGE_INSERT = """INSERT INTO customs_stage
    (trade_month,country_code,hs10_code,hs6_code,hs_ver,
     export_amount,export_weight,import_amount,import_weight)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"""
RAW_JOIN = """m.trade_month=s.trade_month AND m.country_code=s.country_code
    AND m.hs10_code=s.hs10_code AND m.hs_ver=s.hs_ver"""
GROUP_JOIN = """m.trade_month=k.trade_month AND m.country_code=k.country_code
    AND m.hs6_code=k.hs6_code AND m.hs_ver=k.hs_ver"""
KEYS = "(SELECT DISTINCT trade_month,country_code,hs6_code,hs_ver FROM customs_stage)"


def safe_error(exc):
    """DB 예외의 본문에는 접속 정보가 포함될 수 있어 종류와 숫자 코드만 반환한다."""
    code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
    return {"error_type": type(exc).__name__, "db_error_code": code,
            "reason": str(exc) if isinstance(exc, (ValidationError, ConfigurationError))
            else "처리 실패; 파일 경로·의존성·DB 설정/서버 로그 확인"}


class Loader:
    def __init__(self, connection, log):
        self.connection = connection
        self.log = log

    def preflight(self, stage, hs_ver, source_code):
        if not re.fullmatch(r"[A-Z0-9_]{1,10}", hs_ver):
            raise ValidationError("hs_ver: 대문자·숫자·밑줄 1~10자 필요")
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT VERSION(), DATABASE()")
            version, database = cursor.fetchone()
            if not version.startswith("8.4.") or not database:
                raise ValidationError("승인 스키마는 MySQL 8.4의 명시된 DB에서 실행해야 함")
            cursor.execute("SELECT @@SESSION.foreign_key_checks, @@SESSION.sql_mode")
            fk, mode = cursor.fetchone()
            if fk != 1 or not ({"STRICT_TRANS_TABLES", "STRICT_ALL_TABLES"} & set(mode.split(","))):
                raise ValidationError("FK 검사와 strict SQL 모드가 필요함")
            cursor.execute("""SELECT table_name,engine FROM information_schema.tables
                WHERE table_schema=DATABASE() AND table_name IN
                ('export_monthly','import_monthly','export_summary','import_summary','batch_run')""")
            engines = dict(cursor.fetchall())
            if any(engines.get(table) != "InnoDB" for table in (*TABLES, "batch_run")):
                raise ValidationError("원본·집계·batch_run은 승인 DDL의 InnoDB 테이블이어야 함")
            cursor.execute("""SELECT table_name,column_name,extra FROM information_schema.columns
                WHERE table_schema=DATABASE() AND table_name IN ('export_monthly','import_monthly')
                AND column_name IN ('hs4_code','hs6_code')""")
            generated = {(t, c) for t, c, extra in cursor.fetchall() if "STORED GENERATED" in extra}
            if len(generated) != 4:
                raise ValidationError("hs4_code/hs6_code 자동 생성 컬럼이 승인 DDL과 다름")
            self.check_keys(cursor)
            cursor.execute("SELECT option_type,enabled FROM service_option WHERE option_code=%s", (source_code,))
            if cursor.fetchone() != ("SOURCE", 1):
                raise ValidationError("source_code는 활성화된 SOURCE 옵션으로 사전 등록해야 함")
            countries, codes = stage.references()
            cursor.execute("SELECT country_code,country_role FROM country")
            available_countries = {code for code, role in cursor.fetchall() if role in ("COUNTRY", "SPECIAL")}
            cursor.execute("SELECT hs_code FROM hs_code WHERE hs_ver=%s", (hs_ver,))
            available_codes = {r[0] for r in cursor.fetchall()}
            missing = {"country": sorted(countries - available_countries),
                       "hs_code": sorted(codes - available_codes), "hs_ver": hs_ver}
            if missing["country"] or missing["hs_code"]:
                self.log("reference_missing", **missing)
                raise ValidationError("국가 또는 동일 판본 HS10/HS6 사전 누락; 오류 로그 확인")
        self.connection.rollback()  # 사전 조회 스냅샷을 끝낸다.
        self.log("reference_validated", hs_ver=hs_ver, source_code=source_code)

    @staticmethod
    def check_keys(cursor):
        """기존 DB도 업무 PK와 복합 FK가 실제로 존재하는지 확인한다."""
        cursor.execute("""SELECT table_name,constraint_name,column_name,
            referenced_table_name,referenced_column_name FROM information_schema.key_column_usage
            WHERE table_schema=DATABASE() AND table_name IN
            ('export_monthly','import_monthly','export_summary','import_summary','batch_run')
            ORDER BY table_name,constraint_name,ordinal_position""")
        keys = defaultdict(list)
        for table, name, column, parent, parent_column in cursor.fetchall():
            keys[table, name].append((column, parent, parent_column))
        for table in TABLES:
            hs_column = "hs10_code" if table.endswith("monthly") else "hs6_code"
            primary = tuple(c[0] for c in keys[table, "PRIMARY"])
            if primary != ("trade_month", "country_code", hs_column, "hs_ver"):
                raise ValidationError(f"{table}: 업무 PK가 승인 DDL과 다름")
            expected = [[("country_code", "country", "country_code")],
                        [(hs_column, "hs_code", "hs_code"), ("hs_ver", "hs_code", "hs_ver")]]
            if table.endswith("monthly"):
                expected.append([("hs6_code", "hs_code", "hs_code"), ("hs_ver", "hs_code", "hs_ver")])
            actual = [columns for (name, _), columns in keys.items() if name == table]
            if any(fk not in actual for fk in expected):
                raise ValidationError(f"{table}: 국가/HS 복합 FK가 승인 DDL과 다름")

    def load(self, stage, hs_ver, source_code="CUSTOMS", batch_size=2000):
        """이 적재 프로그램끼리 DB 단위 잠금으로 동시 실행을 막는다."""
        if not stage.validated or batch_size <= 0:
            raise ValidationError("오류·중복이 없는 CSV 검증과 양수 batch_size가 필요함")
        with self.connection.cursor() as cursor:
            cursor.execute("SET time_zone='+09:00'")
            cursor.execute("SELECT DATABASE()")
            database = cursor.fetchone()[0]
            lock = "customs_load:" + hashlib.sha256(database.encode()).hexdigest()[:48]
            cursor.execute("SELECT GET_LOCK(%s,0)", (lock,))
            if cursor.fetchone()[0] != 1:
                raise ValidationError("다른 수출입 적재 작업이 실행 중임")
        try:
            self.preflight(stage, hs_ver, source_code)
            with self.connection.cursor() as cursor:
                cursor.execute(STAGE_DDL)
            result = []
            for month in stage.months():
                result.append(self.publish_month(stage, month, hs_ver, source_code, batch_size))
            return result
        finally:
            self.connection.rollback()
            with self.connection.cursor() as cursor:
                cursor.execute("DROP TEMPORARY TABLE IF EXISTS customs_stage")
                cursor.execute("SELECT RELEASE_LOCK(%s)", (lock,))

    def publish_month(self, stage, month, hs_ver, source_code, batch_size):
        started = perf_counter()
        # 임시 영역 INSERT도 나눠 커밋한다. 아직 공개 원본을 수정하지 않는다.
        with self.connection.cursor() as cursor:
            cursor.execute("DELETE FROM customs_stage")
        self.connection.commit()
        count = 0
        for batch in stage.rows(month, batch_size):
            values = [(m, c, h, h[:6], hs_ver, ea, ew, ia, iw) for m, c, h, ea, ew, ia, iw in batch]
            with self.connection.cursor() as cursor:
                cursor.executemany(STAGE_INSERT, values)
            self.connection.commit()
            count += len(values)
        start = date.fromisoformat(month)
        end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
        run_ids = {}
        with self.connection.cursor() as cursor:
            for table in TABLES:
                cursor.execute("""INSERT INTO batch_run
                    (source_code,job_name,target_table,period_start,period_end,status,request_count,received_count)
                    VALUES (%s,%s,%s,%s,%s,'RUNNING',0,%s)""",
                    (source_code, "CUSTOMS_CSV_LOAD" if table.endswith("monthly") else "HS6_AGGREGATION",
                     table, start, end, count))
                run_ids[table] = cursor.lastrowid
        self.connection.commit()
        self.log("month_started", trade_month=month, rows=count, run_ids=run_ids)
        committing = False
        try:
            self.connection.begin()
            with self.connection.cursor() as cursor:
                for direction in ("export", "import"):
                    table = f"{direction}_monthly"
                    # 동일 관측값을 다른 판본으로 중복 공개하지 않는다. 자동 판본 교체도 하지 않는다.
                    cursor.execute(f"""SELECT COUNT(*) FROM {table} m JOIN customs_stage s
                        ON m.trade_month=s.trade_month AND m.country_code=s.country_code
                        AND m.hs10_code=s.hs10_code WHERE m.hs_ver<>s.hs_ver""")
                    if cursor.fetchone()[0]:
                        raise ValidationError(f"{month}: 다른 판본의 동일 관측값이 존재함; 별도 정정 절차 필요")
                    self.merge_raw(cursor, direction, month)
                    self.merge_summary(cursor, direction)
                    self.verify(cursor, direction)
                for run_id in run_ids.values():
                    cursor.execute("UPDATE batch_run SET status='SUCCESS',finished_at=NOW() WHERE run_id=%s", (run_id,))
            # 원본 2개 + 집계 2개 + SUCCESS를 한 번에 공개한다.
            committing = True
            self.connection.commit()
        except Exception as exc:
            if committing:
                # COMMIT 응답 손실은 성공/실패를 추정할 수 없다. 새 연결로 run_id를 확인해야 한다.
                self.log("commit_unknown", trade_month=month, run_ids=run_ids, **safe_error(exc))
                raise
            self.connection.rollback()
            with self.connection.cursor() as cursor:
                for run_id in run_ids.values():
                    cursor.execute("UPDATE batch_run SET status='FAILED',finished_at=NOW() WHERE run_id=%s", (run_id,))
            self.connection.commit()
            self.log("month_failed", trade_month=month, run_ids=run_ids, **safe_error(exc))
            raise
        self.log("month_success", trade_month=month, rows=count, run_ids=run_ids,
                 elapsed_seconds=round(perf_counter() - started, 3))
        return {"trade_month": month, "rows": count, "run_ids": run_ids, "status": "SUCCESS"}

    @staticmethod
    def merge_raw(cursor, direction, month):
        table = f"{direction}_monthly"
        cursor.execute(f"""UPDATE customs_stage s JOIN {table} m ON {RAW_JOIN}
            SET s.{direction}_changed = NOT ((m.amount_usd <=> s.{direction}_amount)
            AND (m.weight_kg <=> s.{direction}_weight))""")
        # 정정·새 관측값으로 과거/미래 판정 근거가 달라지는 범위를 미계산으로 돌린다.
        cursor.execute(f"""UPDATE {table} m JOIN customs_stage s
            ON m.country_code=s.country_code AND m.hs10_code=s.hs10_code AND m.hs_ver=s.hs_ver
            AND s.trade_month=%s
            SET m.history_months=NULL,m.baseline_amount_usd=NULL,
                m.median_unit_usd_kg=NULL,m.anomaly_code=NULL
            WHERE s.{direction}_changed=1
                AND m.trade_month BETWEEN DATE_SUB(%s,INTERVAL 1 MONTH)
                    AND DATE_ADD(%s,INTERVAL 12 MONTH)
                AND (m.history_months IS NOT NULL OR m.baseline_amount_usd IS NOT NULL
                    OR m.median_unit_usd_kg IS NOT NULL OR m.anomaly_code IS NOT NULL)""",
                       (month, month, month))
        cursor.execute(f"""UPDATE {table} m JOIN customs_stage s ON {RAW_JOIN}
            SET m.amount_usd=s.{direction}_amount,m.weight_kg=s.{direction}_weight
            WHERE s.{direction}_changed=1""")
        cursor.execute(f"""INSERT INTO {table}
            (trade_month,country_code,hs10_code,hs_ver,amount_usd,weight_kg)
            SELECT s.trade_month,s.country_code,s.hs10_code,s.hs_ver,s.{direction}_amount,s.{direction}_weight
            FROM customs_stage s LEFT JOIN {table} m ON {RAW_JOIN}
            WHERE m.hs10_code IS NULL""")

    @staticmethod
    def aggregate(direction):
        # 입력에서 빠진 원본을 삭제하지 않고 영향받은 HS6를 공개 원본 전체로 재계산한다.
        return f"""SELECT m.trade_month,m.country_code,m.hs6_code,m.hs_ver,SUM(m.amount_usd) AS total
            FROM {direction}_monthly m JOIN {KEYS} k ON {GROUP_JOIN}
            GROUP BY m.trade_month,m.country_code,m.hs6_code,m.hs_ver"""

    @classmethod
    def merge_summary(cls, cursor, direction):
        table = f"{direction}_summary"
        aggregate = cls.aggregate(direction)
        # 대상 집계를 다시 LEFT JOIN하면 INSERT SELECT가 자기 참조 결과를
        # 별도로 버퍼링한다. 업무 PK로 upsert하여 집계 계산과 저장을 한 번에 한다.
        cursor.execute(f"""INSERT INTO {table} (trade_month,country_code,hs6_code,hs_ver,amount_usd)
            SELECT a.trade_month,a.country_code,a.hs6_code,a.hs_ver,a.total
            FROM ({aggregate}) a ON DUPLICATE KEY UPDATE amount_usd=a.total""")

    @classmethod
    def verify(cls, cursor, direction):
        cursor.execute(f"""SELECT COUNT(*) FROM customs_stage s LEFT JOIN {direction}_monthly m ON {RAW_JOIN}
            WHERE m.hs10_code IS NULL OR NOT (m.amount_usd <=> s.{direction}_amount)
                OR NOT (m.weight_kg <=> s.{direction}_weight)""")
        if cursor.fetchone()[0]:
            raise ValidationError(f"{direction}: CSV와 적재 원본 불일치")
        aggregate = cls.aggregate(direction)
        cursor.execute(f"""SELECT COUNT(*) FROM ({aggregate}) a LEFT JOIN {direction}_summary t
            ON t.trade_month=a.trade_month AND t.country_code=a.country_code
            AND t.hs6_code=a.hs6_code AND t.hs_ver=a.hs_ver
            WHERE t.hs6_code IS NULL OR t.amount_usd<>a.total""")
        if cursor.fetchone()[0]:
            raise ValidationError(f"{direction}: 원본과 HS6 집계 불일치")
