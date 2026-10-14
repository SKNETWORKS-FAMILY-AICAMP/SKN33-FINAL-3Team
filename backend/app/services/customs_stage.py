"""관세청 CSV를 문자열/Decimal로 검증하고 디스크 임시 영역에 저장한다.

SQLite는 서비스 DB가 아닌 작업 공간이다. 전체 CSV를 메모리에 올리지 않는다.
"""
import csv
import json
import re
import sqlite3
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

FIELDS = ("year", "statCd", "hsCd", "expDlr", "expWgt", "impDlr", "impWgt")


class ValidationError(ValueError):
    """원본 또는 사전 검증 실패. 공개 테이블에 반영하지 않는다."""


def number(value, field, scale, nullable=False):
    value = (value or "").strip()
    if not value and nullable:
        return None
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
        raise ValidationError(f"{field}: 비음수 십진수가 아님")
    try:
        with localcontext() as ctx:
            ctx.prec = 40
            amount = Decimal(value)
            rounded = amount.quantize(Decimal(1).scaleb(-scale))
            if amount != rounded or amount >= Decimal(10) ** (22 - scale):
                raise ValidationError(f"{field}: DECIMAL(22,{scale}) 범위/소수 자릿수 초과")
            return format(rounded, "f")
    except InvalidOperation as exc:
        raise ValidationError(f"{field}: 십진수 범위 초과") from exc


def normalize(row, year):
    """총계는 None, 유효 행은 (월, 국가, HS10, 수출금액/중량, 수입금액/중량)."""
    if None in row or any(row.get(field) is None for field in FIELDS):
        raise ValidationError("CSV 열 수 불일치")
    month = row["year"].strip()
    if month == "총계":
        if row["statCd"].strip() != "-" or row["hsCd"].strip() != "-":
            raise ValidationError("총계 행의 국가/HS 코드가 예상 형식과 다름")
        return None
    if not re.fullmatch(r"[0-9]{4}\.(0[1-9]|1[0-2])", month):
        raise ValidationError("year: YYYY.MM 월 형식이 아님")
    if int(month[:4]) != year:
        raise ValidationError("year: 요청한 연도와 다름")
    country = row["statCd"].strip()
    hs10 = row["hsCd"].strip()
    # NA(나미비아)를 결측으로 변환하지 않고 HS 선행 0도 보존한다.
    if not re.fullmatch(r"[A-Z0-9_]{1,12}", country):
        raise ValidationError("statCd: 국가 코드 형식 오류")
    if not re.fullmatch(r"[0-9]{10}", hs10):
        raise ValidationError("hsCd: HS10 숫자 문자열이 아님")
    return (
        date(year, int(month[5:]), 1).isoformat(), country, hs10,
        number(row["expDlr"], "expDlr", 2),
        number(row["expWgt"], "expWgt", 3, nullable=True),
        number(row["impDlr"], "impDlr", 2),
        number(row["impWgt"], "impWgt", 3, nullable=True),
    )


class DecimalSum:
    """SQLite의 실수 SUM 대신 정수 센트 단위로 정확히 합산한다."""

    def __init__(self):
        self.cents = 0

    def step(self, value):
        if value is not None:
            self.cents += int(Decimal(value) * 100)

    def finalize(self):
        return f"{self.cents // 100}.{self.cents % 100:02d}"


class Stage:
    def __init__(self, path):
        self.path = Path(path)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA temp_store=FILE")
        self.connection.execute("PRAGMA cache_size=-16384")
        self.connection.create_aggregate("decimal_sum", 1, DecimalSum)
        self.connection.execute("""CREATE TABLE trade_rows (
            trade_month TEXT NOT NULL, country_code TEXT NOT NULL,
            hs10_code TEXT NOT NULL, export_amount TEXT NOT NULL,
            export_weight TEXT, import_amount TEXT NOT NULL, import_weight TEXT,
            PRIMARY KEY (trade_month,country_code,hs10_code)
        ) WITHOUT ROWID""")
        self.validated = False

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def ingest(self, csv_path, year, errors_path, batch_size=2000):
        if not 1 <= year <= 9999 or batch_size <= 0:
            raise ValueError("유효한 연도와 양수 batch_size가 필요함")
        counts = Counter()
        with Path(csv_path).open(encoding="utf-8-sig", newline="") as src, \
                Path(errors_path).open("w", encoding="utf-8") as errors:
            reader = csv.DictReader(src)
            if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise ValidationError("CSV 헤더가 없거나 중복됨")
            missing = set(FIELDS) - set(reader.fieldnames)
            if missing:
                raise ValidationError(f"필수 CSV 열 누락: {', '.join(sorted(missing))}")
            for record, row in enumerate(reader, 1):
                counts["rows_total"] += 1
                try:
                    values = normalize(row, year)
                    if values is None:
                        counts["rows_total_line"] += 1
                        continue
                    self.connection.execute("INSERT INTO trade_rows VALUES (?,?,?,?,?,?,?)", values)
                    counts["rows_valid"] += 1
                except (ValidationError, sqlite3.IntegrityError) as exc:
                    duplicate = isinstance(exc, sqlite3.IntegrityError)
                    counts["duplicate_keys" if duplicate else "invalid_rows"] += 1
                    errors.write(json.dumps({
                        "record": record, "line_end": reader.line_num,
                        "key": {k: row.get(k) for k in ("year", "statCd", "hsCd")},
                        "reason": "업무 키 중복" if duplicate else str(exc),
                    }, ensure_ascii=False) + "\n")
                if record % batch_size == 0:
                    self.connection.commit()
        self.connection.commit()
        self.validated = bool(counts["rows_valid"] and not counts["invalid_rows"] and not counts["duplicate_keys"])
        return {key: counts[key] for key in (
            "rows_total", "rows_valid", "rows_total_line", "invalid_rows", "duplicate_keys")}

    def months(self):
        return [row[0] for row in self.connection.execute(
            "SELECT DISTINCT trade_month FROM trade_rows ORDER BY trade_month")]

    def rows(self, month, batch_size=2000):
        cursor = self.connection.execute(
            "SELECT * FROM trade_rows WHERE trade_month=? ORDER BY country_code,hs10_code", (month,))
        while batch := cursor.fetchmany(batch_size):
            yield batch

    def references(self):
        countries = {r[0] for r in self.connection.execute("SELECT DISTINCT country_code FROM trade_rows")}
        hs10 = {r[0] for r in self.connection.execute("SELECT DISTINCT hs10_code FROM trade_rows")}
        return countries, hs10 | {code[:6] for code in hs10}

    def hold_unmapped_hs(self, csv_path, year, output_path, available_codes):
        """명시적으로 선택한 보류 모드에서만 원문을 내보낸 뒤 작업 공간에서 뺀다."""
        if not self.validated:
            raise ValidationError("보류 전 전체 CSV 검증이 필요함")
        _, required = self.references()
        missing = {code for code in required if len(code) == 10 and
                   (code not in available_codes or code[:6] not in available_codes)}
        expected = sum(self.connection.execute(
            "SELECT COUNT(*) FROM trade_rows WHERE hs10_code=?", (code,)).fetchone()[0]
            for code in missing)
        held = 0
        with Path(csv_path).open(encoding="utf-8-sig", newline="") as source, \
                Path(output_path).open("w", encoding="utf-8-sig", newline="") as destination:
            reader = csv.DictReader(source)
            metadata = ["_hold_record", "_hold_reason"]
            if not reader.fieldnames or set(metadata) & set(reader.fieldnames):
                raise ValidationError("보류 파일 메타데이터 컬럼과 원본 헤더 충돌")
            writer = csv.DictWriter(destination, fieldnames=[*reader.fieldnames, *metadata])
            writer.writeheader()
            for record, row in enumerate(reader, 1):
                code = row.get("hsCd", "").strip()
                if code not in missing or row.get("year", "").strip() == "총계":
                    continue
                values = normalize(row, year)
                stored = self.connection.execute("SELECT * FROM trade_rows WHERE trade_month=? AND country_code=? AND hs10_code=?", values[:3]).fetchone()
                if stored != values:
                    raise ValidationError("검증 이후 보류 대상 CSV가 변경됨")
                reason = "HS10 사전 누락" if code not in available_codes else "동일 판본 HS6 사전 누락"
                writer.writerow({**row, "_hold_record": record, "_hold_reason": reason})
                held += 1
        if held != expected:
            raise ValidationError("검증 이후 보류 대상 CSV 행 수가 변경됨")
        for code in missing:
            self.connection.execute("DELETE FROM trade_rows WHERE hs10_code=?", (code,))
        self.connection.commit()
        if not self.connection.execute("SELECT 1 FROM trade_rows LIMIT 1").fetchone():
            self.validated = False
            raise ValidationError("모든 유효 행이 보류되어 적재할 행이 없음")
        return {"rows_held": held, "held_hs10": sorted(missing), "held_file": Path(output_path).name}

    def summary(self):
        result = []
        for month in self.months():
            row = self.connection.execute("""SELECT COUNT(*),
                decimal_sum(export_amount), decimal_sum(import_amount)
                FROM trade_rows WHERE trade_month=?""", (month,)).fetchone()
            grouped = self.connection.execute("""SELECT country_code, SUBSTR(hs10_code,1,6),
                decimal_sum(export_amount), decimal_sum(import_amount)
                FROM trade_rows WHERE trade_month=? GROUP BY country_code,SUBSTR(hs10_code,1,6)""", (month,))
            # 집계 후에도 정수 센트 단위로 정확한 합계를 유지한다.
            cents = [0, 0]
            groups = 0
            for group in grouped:
                groups += 1
                for direction, index in enumerate((2, 3)):
                    amount = Decimal(group[index])
                    if amount >= Decimal(10) ** 22:
                        raise ValidationError(f"{month}: HS6 합계가 DECIMAL(24,2) 범위를 초과")
                    cents[direction] += int(amount * 100)
            exact = [f"{v // 100}.{v % 100:02d}" for v in cents]
            if exact != [row[1], row[2]]:
                raise ValidationError(f"{month}: 원본과 HS6 집계의 금액 불일치")
            result.append({"trade_month": month, "rows": row[0], "hs6_groups": groups,
                           "export_usd": row[1], "import_usd": row[2], "hs6_totals_match": True})
        return result
