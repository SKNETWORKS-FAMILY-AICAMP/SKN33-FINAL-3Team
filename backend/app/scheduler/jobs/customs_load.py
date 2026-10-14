"""실행: python -m backend.app.scheduler.jobs.customs_load --year 2025

기본 CSV: 저장소 루트/data/raw/customs/customs_raw_<연도>.csv
"""
import argparse
import json
import sys
import tempfile
from time import perf_counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.db.mysql import ENV_PATH, connect_mysql
from backend.app.services.customs_load import Loader, safe_error
from backend.app.services.customs_stage import Stage, ValidationError
from backend.app.services.customs_metrics import MetricCalculator

DEFAULT_OUTPUT = Path(__file__).resolve().parents[4] / "data/customs_load_outputs"
DEFAULT_CSV_DIR = ENV_PATH.parent / "data" / "raw" / "customs"


def run(args):
    started = perf_counter()
    work = args.output / datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d_%H%M%S_%f")
    work.mkdir(parents=True, exist_ok=False)
    report = {"year": args.year, "file": args.csv.name, "mode": args.mode,
              "hs_ver": args.hs_ver, "status": "RUNNING", "database_loaded": False,
              "unmapped_policy": getattr(args, "unmapped_policy", "fail"), "published_months": []}
    with (work / "events.jsonl").open("w", encoding="utf-8") as events:
        def log(event, **values):
            payload = {"at": datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds"),
                       "event": event, **values}
            events.write(json.dumps(payload, ensure_ascii=False) + "\n")
            events.flush()
            if event == "month_success":
                report["database_loaded"] = True
                report["published_months"].append({**values, "status": "SUCCESS"})
            elif event == "commit_unknown":
                report["commit_unknown"] = values
            elif event == "reference_validated":
                report["dictionary_verification"] = "PASSED"

        try:
            # 출력 폴더에 자동 정리할 작업 영역을 만든다. 결과와 오류 로그는 보존한다.
            with tempfile.TemporaryDirectory(prefix="stage_", dir=work) as temporary, \
                    Stage(Path(temporary) / "trade.sqlite3") as stage:
                report.update(stage.ingest(args.csv, args.year, work / "errors.jsonl", args.batch_size))
                log("csv_validated", **{k: report[k] for k in (
                    "rows_total", "rows_valid", "rows_total_line", "invalid_rows", "duplicate_keys")})
                report["months"] = stage.summary()
                countries, codes = stage.references()
                report["reference_counts"] = {"countries": len(countries),
                                               "hs10": sum(len(c) == 10 for c in codes),
                                               "hs6": sum(len(c) == 6 for c in codes)}
                report["dictionary_verification"] = "NOT_RUN"
                report["missing_months"] = [f"{args.year:04d}-{m:02d}-01" for m in range(1, 13)
                                            if f"{args.year:04d}-{m:02d}-01" not in stage.months()]
                if report["invalid_rows"] or report["duplicate_keys"] or not report["rows_valid"]:
                    raise ValidationError("무효·중복 행이 있거나 유효 행이 0건임; errors.jsonl 확인")
                if args.mode == "load":
                    connection = connect_mysql(args.env)
                    try:
                        if getattr(args, "unmapped_policy", "fail") == "hold":
                            with connection.cursor() as cursor:
                                cursor.execute("SELECT hs_code FROM hs_code WHERE hs_ver=%s", (args.hs_ver,))
                                available_codes = {r[0] for r in cursor.fetchall()}
                            connection.rollback()
                            report["input_months"] = report["months"]
                            report.update(stage.hold_unmapped_hs(args.csv, args.year,
                                work / "held_rows.csv", available_codes))
                            report["rows_eligible"] = report["rows_valid"] - report["rows_held"]
                            report["months"] = stage.summary()
                            log("hs_rows_held", rows=report["rows_held"], codes=report["held_hs10"])
                        Loader(connection, log).load(
                            stage, args.hs_ver, args.source_code, args.batch_size)
                        report["metrics"] = MetricCalculator(connection, log).calculate(args.year, args.batch_size)
                        report["database_loaded"] = True
                        report["dictionary_verification"] = "PASSED"
                    finally:
                        connection.close()
                report["status"] = "LOADED" if args.mode == "load" else "VALIDATED"
                if report.get("rows_held"):
                    report["status"] = "LOADED_WITH_HOLDS"
                log("completed", status=report["status"])
        except Exception as exc:
            report["status"] = "COMMIT_UNKNOWN" if report.get("commit_unknown") else "FAILED"
            report["error"] = safe_error(exc)
            log("failed", **report["error"])
        finally:
            report["elapsed_seconds"] = round(perf_counter() - started, 3)
            (work / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"결과 위치: {work}")
    return 0 if report["status"] in ("VALIDATED", "LOADED", "LOADED_WITH_HOLDS") else 1


def main():
    parser = argparse.ArgumentParser(description="관세청 CSV 한 연도 검증 / 월별 MySQL 적재")
    parser.add_argument("--csv", type=Path,
                        help="CSV 경로 (기본: 저장소 루트/data/raw/customs/customs_raw_<year>.csv)")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--mode", choices=("validate", "load"), default="validate")
    parser.add_argument("--unmapped-policy", choices=("fail", "hold"), default="fail",
                        help="기본 fail: 누락 시 전체 중단. hold: 미연결 HS 행 원문 보존 후 나머지 적재")
    parser.add_argument("--hs-ver", help="실제 원천에서 확인한 HS 판본; load 모드 필수")
    parser.add_argument("--source-code", default="CUSTOMS")
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--env", type=Path, default=ENV_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.mode == "load" and not args.hs_ver:
        parser.error("load 모드는 --hs-ver를 명시해야 합니다 (연도에서 자동 추정하지 않음).")
    if args.unmapped_policy == "hold" and args.mode != "load":
        parser.error("hold는 DB 사전과 대조하는 load 모드에서만 사용합니다.")
    if args.batch_size <= 0 or not 1 <= args.year <= 9999:
        parser.error("batch-size는 양수, year는 1~9999이어야 합니다.")
    if args.csv is None:
        args.csv = DEFAULT_CSV_DIR / f"customs_raw_{args.year}.csv"
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
