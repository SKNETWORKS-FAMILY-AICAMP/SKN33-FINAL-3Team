"""적재용 MySQL 연결. 실제 인증값은 환경변수 또는 루트 .env에서 읽는다."""
import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parents[3] / ".env"


class ConfigurationError(ValueError):
    """접속 값을 노출하지 않는 설정 오류."""


def connect_mysql(env_path=ENV_PATH):
    # validate 모드는 외부 패키지나 DB 연결 없이 실행할 수 있다.
    import pymysql
    from dotenv import dotenv_values

    values = {**dotenv_values(env_path), **os.environ}
    required = ("MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE")
    missing = [key for key in required if values.get(key) is None or
               (key != "MYSQL_PASSWORD" and not values[key]) or
               str(values.get(key, "")).startswith("YOUR_")]
    if missing:
        raise ConfigurationError("MySQL 설정 누락: " + ", ".join(missing))
    try:
        port = int(values.get("MYSQL_PORT", "3306"))
        if not 1 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ConfigurationError("MYSQL_PORT는 1~65535 정수여야 함") from exc
    return pymysql.connect(
        host=values["MYSQL_HOST"], port=port,
        user=values["MYSQL_USER"], password=values["MYSQL_PASSWORD"],
        database=values["MYSQL_DATABASE"], charset="utf8mb4", autocommit=False,
        connect_timeout=10, read_timeout=300, write_timeout=300,
        local_infile=False,
    )
