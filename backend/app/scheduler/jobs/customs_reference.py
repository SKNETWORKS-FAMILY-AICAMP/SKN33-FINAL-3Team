"""실행: python -m backend.app.scheduler.jobs.customs_reference --help"""
import argparse
import json
from pathlib import Path

from backend.app.db.mysql import ENV_PATH, connect_mysql
from backend.app.services.customs_reference import read_countries, read_hsk, seed_references
from backend.app.services.customs_load import safe_error
from backend.app.scheduler.collectors.customs.customs_collect import COUNTRIES

DEFAULT_CSV_DIR = ENV_PATH.parent / "data" / "raw" / "customs"


def main():
    parser = argparse.ArgumentParser(description="공식 HSK 연도별 시트와 관세청 CSV 국가 기준정보 준비")
    parser.add_argument("--csv", type=Path,
                        help="CSV 경로 (기본: 저장소 루트/data/raw/customs/customs_raw_<year>.csv)")
    parser.add_argument("--hs-xlsx", type=Path, required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--hs-ver", required=True)
    parser.add_argument("--source-edition", required=True)
    parser.add_argument("--mode", choices=("validate", "load"), default="validate")
    parser.add_argument("--refresh-names", action="store_true",
                        help="같은 출처의 HS4 원명을 복원하고 HS6만 HS4·HS6 원문을 합친 요약명으로 갱신")
    parser.add_argument("--env", type=Path, default=ENV_PATH)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.csv is None:
        args.csv = DEFAULT_CSV_DIR / f"customs_raw_{args.year}.csv"
    report = {"year": args.year, "hs_ver": args.hs_ver, "source_edition": args.source_edition,
              "hs_file": args.hs_xlsx.name, "csv_file": args.csv.name, "database_loaded": False}
    try:
        hs, stats = read_hsk(args.hs_xlsx, args.year, args.hs_ver, args.source_edition)
        countries = read_countries(args.csv, set(COUNTRIES))
        report.update(stats)
        report["countries"] = len(countries)
        if args.mode == "load":
            connection = connect_mysql(args.env)
            try:
                report["inserted"] = seed_references(connection, countries, hs, args.refresh_names)
                report["database_loaded"] = True
            finally:
                connection.close()
        report["status"] = "LOADED" if args.mode == "load" else "VALIDATED"
    except Exception as exc:
        report["status"] = "FAILED"
        report["error"] = safe_error(exc)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["status"] == "FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
