"""실행: python -m backend.app.scheduler.jobs.customs_calculate --year 2025"""
import argparse
import json
from pathlib import Path
from time import perf_counter

from backend.app.db.mysql import ENV_PATH, connect_mysql
from backend.app.services.customs_metrics import MetricCalculator, RULE_CODE, validate_rules
from backend.app.services.customs_stage import ValidationError


def main():
    parser = argparse.ArgumentParser(description="적재된 수출입 원본의 이력·일회성 가능성 계산")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--env", type=Path, default=ENV_PATH)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--r1-spike-ratio", type=float,
                        help="확정한 R1 급증 배수 N. r1-return-pct와 함께 지정")
    parser.add_argument("--r1-return-pct", type=float,
                        help="확정한 R1 복귀 허용 범위 ±M%%. r1-spike-ratio와 함께 지정")
    parser.add_argument("--r3-unit-ratio", type=float,
                        help="확정한 R3 단가 배수 K. 고가 K배 이상·저가 1/K 이하 판정")
    parser.add_argument("--r2-hs-prefixes", nargs="+",
                        help="팀에서 확정한 R2 HS 목록을 RULE에 등록 (예: 88 89). R1·R3는 유지")
    args = parser.parse_args()
    if not 2 <= args.year <= 9997:
        parser.error("year는 2~9997이어야 합니다")
    if (args.r1_spike_ratio is None) != (args.r1_return_pct is None):
        parser.error("R1은 --r1-spike-ratio와 --r1-return-pct를 함께 지정해야 합니다")
    started = perf_counter()
    connection = connect_mysql(args.env)
    try:
        if any(value is not None for value in (
                args.r1_spike_ratio, args.r2_hs_prefixes, args.r3_unit_ratio)):
            with connection.cursor() as cursor:
                cursor.execute("SELECT option_type,value_json FROM service_option WHERE option_code=%s FOR UPDATE", (RULE_CODE,))
                existing = cursor.fetchone()
                if existing and existing[0] != "RULE":
                    raise ValidationError("기존 일회성 옵션이 RULE 종류가 아님")
                rules = json.loads(existing[1]) if existing else {}
                if args.r1_spike_ratio is not None:
                    rules["r1"] = {"spike_ratio": args.r1_spike_ratio,
                                   "return_pct": args.r1_return_pct}
                if args.r2_hs_prefixes is not None:
                    rules["r2"] = {"hs_prefixes": args.r2_hs_prefixes}
                if args.r3_unit_ratio is not None:
                    rules["r3"] = {"unit_ratio": args.r3_unit_ratio}
                validate_rules(rules)
                if existing:
                    cursor.execute("UPDATE service_option SET value_json=%s,enabled=1 WHERE option_code=%s", (json.dumps(rules),RULE_CODE))
                else:
                    cursor.execute("INSERT INTO service_option (option_code,option_type,label_ko,value_json) VALUES (%s,'RULE','수출입 일회성 가능성',%s)", (RULE_CODE,json.dumps(rules)))
            connection.commit()
        result = MetricCalculator(connection, lambda event, **values:
            print(json.dumps({"event": event, **values}, ensure_ascii=False), flush=True)).calculate(args.year)
    finally:
        connection.close()
    result["elapsed_seconds"] = round(perf_counter() - started, 3)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
