-- 3팀 무역통계 Agent 2026-10-13 HS 공통 판본 반영 MySQL 8.4

-- 빈 DB 초기 설치용 DDL. 기존 DB를 자동 변경하거나 삭제하지 않습니다.

SET NAMES utf8mb4;

CREATE TABLE users (
  user_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '회원 한 명을 구분하는 내부 번호다. 자동으로 부여한다.',
  email VARCHAR(254) NOT NULL COMMENT '로그인에 사용하는 이메일 주소다. 같은 주소를 중복 등록하지 않는다.',
  password_hash VARCHAR(255) NOT NULL COMMENT 'bcrypt로 계산한 비밀번호 해시 문자열이다. 평문 비밀번호는 저장하지 않는다.',
  landing_page ENUM('GLOBAL','INTERESTED') NOT NULL DEFAULT 'INTERESTED' COMMENT '로그인 후 첫 화면을 GLOBAL 전체 시장 또는 INTERESTED 관심 시장으로 선택한다.',
  PRIMARY KEY (user_id),
  UNIQUE KEY uq_users_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE service_option (
  option_code VARCHAR(40) NOT NULL COMMENT '설정 하나의 고유 코드다. 예를 들어 MONTHLY이며, 이 표의 PK다.',
  option_type ENUM('SOURCE','REPORT_PERIOD','REPORT_TYPE','RULE','NOTIFICATION_TERMS') NOT NULL COMMENT '출처·구독 주기·리포트 종류·판정 규칙·약관 중 어떤 선택지 그룹인지 구분한다.',
  label_ko VARCHAR(120) NOT NULL COMMENT '사람이 읽는 한글 이름이다. 예를 들어 월간 리포트다.',
  value_json JSON NOT NULL COMMENT '설정의 구체 내용을 묶은 객체다. 주기는 발행 조건, 출처는 URL·한도, 규칙은 임계값을 담는다.',
  sort_order SMALLINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '같은 그룹 안에서 화면에 표시할 순서다. 작은 수를 먼저 표시한다.',
  enabled BOOLEAN NOT NULL DEFAULT TRUE COMMENT '서비스가 현재 이 설정을 선택·사용하도록 허용하는지 저장한다.',
  PRIMARY KEY (option_code),
  KEY ix_option_selection (option_type, enabled, sort_order, option_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE market_region (
  region_id SMALLINT UNSIGNED NOT NULL COMMENT '대륙 또는 하위 지역 하나를 식별하는 내부 번호다.',
  parent_id SMALLINT UNSIGNED NULL COMMENT '하위 지역이 속한 대륙의 region_id다. 대륙 행에는 상위가 없어 NULL이다.',
  region_level TINYINT UNSIGNED NOT NULL COMMENT '1은 대륙, 2는 대륙 안의 하위 지역이다. 국가 단계는 이 표에 넣지 않는다.',
  name_ko VARCHAR(80) NOT NULL COMMENT '아시아·동남아시아처럼 화면에 표시하는 지역 이름이다.',
  PRIMARY KEY (region_id),
  UNIQUE KEY uq_region_name (name_ko),
  FOREIGN KEY (parent_id) REFERENCES market_region (region_id),
  CHECK ((region_level=1 AND parent_id IS NULL) OR (region_level=2 AND parent_id IS NOT NULL AND parent_id<>region_id))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE currency (
  currency_code VARCHAR(12) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT 'USD·JPY처럼 통화 하나를 식별하는 코드다.',
  name_ko VARCHAR(80) NOT NULL COMMENT '미국 달러·일본 엔처럼 사람이 읽는 통화 이름이다.',
  ecos_item_code VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL COMMENT 'ECOS 환율 API에서 이 통화를 요청할 때 사용하는 항목 코드다. 미제공이면 NULL이다.',
  quote_unit SMALLINT UNSIGNED NOT NULL COMMENT '환율의 기준 외화 수량이다. 예를 들어 달러는 1, 엔은 100처럼 저장한다.',
  PRIMARY KEY (currency_code),
  UNIQUE KEY uq_currency_item (ecos_item_code),
  CHECK (quote_unit>0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE hs_code (
  hs_code VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '품목을 구분하는 HS 문자열이다. 2·4·6·10자리 코드를 함께 담는다.',
  depth TINYINT UNSIGNED GENERATED ALWAYS AS (CHAR_LENGTH(hs_code)) STORED COMMENT 'HS 코드의 자릿수다. 2·4·6·10 중 하나이며 DB가 코드 길이로 자동 계산한다.',
  hs_ver VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '코드의 공통 사전 판본 식별값. 예: HS2017, HS2022.',
  name_ko VARCHAR(500) NOT NULL COMMENT '화면에 표시하고 검색할 한국어 품목명이다. HS6만 HS4·HS6 품목명을 합친 요약명을 저장하고 HS2·HS4·HS10은 해당 분류의 원래 품목명을 저장한다.',
  original_name_ko TEXT NULL COMMENT '공식 원천의 HS6 한국어 품목명 원문이다. depth=6에만 저장하고 HS4·HS2·HS10은 NULL이다.',
  source_edition VARCHAR(80) NOT NULL COMMENT '품목명을 확인한 공식 사전의 판본·기준 정보를 저장한다.',
  PRIMARY KEY (hs_code, hs_ver),
  CHECK (CHAR_LENGTH(hs_code) IN (2,4,6,10) AND hs_code REGEXP '^[0-9]+$'),
  CHECK ((CHAR_LENGTH(hs_code)=6 AND original_name_ko IS NOT NULL AND CHAR_LENGTH(TRIM(original_name_ko))>0) OR (CHAR_LENGTH(hs_code)<>6 AND original_name_ko IS NULL)),
  KEY ix_hs_depth (depth, hs_ver, hs_code),
  CHECK (hs_ver REGEXP '^[A-Z0-9_]+$')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE model_run (
  model_run_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '국가 군집 모델을 한 번 실행한 기록의 번호다.',
  train_start DATE NOT NULL COMMENT '군집 계산에 사용한 수출 통계 기간의 시작일이다.',
  train_end DATE NOT NULL COMMENT '군집 계산에 사용한 통계 기간의 마지막 기준일이다.',
  model_name VARCHAR(120) NOT NULL COMMENT '사용한 K-Means 모델이나 버전을 구분하는 이름이다.',
  status ENUM('RUNNING','SUCCESS','FAILED') NOT NULL DEFAULT 'RUNNING' COMMENT 'RUNNING 계산 중, SUCCESS 정상 완료, FAILED 실패 상태다.',
  metadata_json JSON NOT NULL COMMENT '입력 변수·정규화·군집 수·라벨 해석·평가 값 등을 묶어 저장한다.',
  country_results_json JSON NULL COMMENT '국가코드별 라벨·성장률·규모·변동성의 작은 결과 배열이다. 미완료는 NULL 가능하다.',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '모델 실행 기록을 만든 시각이다. 계산 완료 시각과 같은 뜻은 아니다.',
  PRIMARY KEY (model_run_id),
  CHECK (train_start<=train_end),
  CHECK (status<>'SUCCESS' OR country_results_json IS NOT NULL)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE notification_setting (
  user_id BIGINT UNSIGNED NOT NULL COMMENT '설정 소유자의 회원 번호다. 회원당 설정 행은 하나다.',
  notifications_enabled BOOLEAN NOT NULL DEFAULT FALSE COMMENT '개인 알림을 전체적으로 켜거나 끄는 스위치다. TRUE는 켜짐이다.',
  report_popup BOOLEAN NOT NULL DEFAULT TRUE COMMENT '내 리포트가 발행됐을 때 팝업을 받을지 저장한다.',
  major_issue_popup BOOLEAN NOT NULL DEFAULT TRUE COMMENT '주요 이슈에 대한 개인 팝업 허용 여부다.',
  spike_popup BOOLEAN NOT NULL DEFAULT TRUE COMMENT '관심 품목 및 키워드의 급증 기사 팝업을 함께 켜거나 끈다.',
  PRIMARY KEY (user_id),
  FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE user_consent (
  consent_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '동의·거부·철회 응답 한 번마다 부여하는 번호다.',
  user_id BIGINT UNSIGNED NOT NULL COMMENT '약관에 응답한 회원의 내부 번호다.',
  terms_version VARCHAR(40) NOT NULL COMMENT '응답한 약관의 고유 코드다. service_option의 약관 행을 참조한다.',
  action ENUM('AGREE','DECLINE','REVOKE') NOT NULL COMMENT 'AGREE 동의, DECLINE 거부, REVOKE 동의 철회 중 실제 응답을 저장한다.',
  acted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '응답을 받은 날짜와 시각을 초 단위로 저장한다.',
  PRIMARY KEY (consent_id),
  FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
  FOREIGN KEY (terms_version) REFERENCES service_option (option_code),
  KEY ix_consent_latest (user_id, acted_at, consent_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE country (
  country_code VARCHAR(12) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '관세청 기준 국가 식별 코드다. 수집·뉴스·관심에서 같은 기준으로 연결한다.',
  name_ko VARCHAR(120) NOT NULL COMMENT '국가 선택과 결과에 표시할 한국어 이름이다.',
  country_role ENUM('COUNTRY','SPECIAL','TOTAL') NOT NULL DEFAULT 'COUNTRY' COMMENT 'COUNTRY 일반 국가, SPECIAL 특수 코드, TOTAL 총계 행의 역할 구분이다.',
  region_id SMALLINT UNSIGNED NULL COMMENT '해당 국가가 속한 하위 지역의 번호다. 미매핑이면 NULL이다.',
  currency_code VARCHAR(12) CHARACTER SET ascii COLLATE ascii_bin NULL COMMENT '환율 카드에 연결할 대표 통화 코드다. 확인되지 않으면 NULL이다.',
  PRIMARY KEY (country_code),
  FOREIGN KEY (region_id) REFERENCES market_region (region_id),
  FOREIGN KEY (currency_code) REFERENCES currency (currency_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE batch_run (
  run_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '테이블·기간별 데이터 작업 실행 한 건의 번호다.',
  source_code VARCHAR(40) NOT NULL COMMENT '이 작업의 대상 출처 코드다. service_option의 SOURCE 행을 참조한다.',
  job_name VARCHAR(60) NOT NULL COMMENT '수행한 작업 이름이다. 예를 들어 NEWS_COLLECTION 또는 HS6_AGGREGATION이다.',
  target_table ENUM('hs_code','export_monthly','import_monthly','export_summary','import_summary','fx_daily','article','article_tag','news_product_daily','news_keyword_daily','model_run','report') NOT NULL COMMENT '이 실행이 처리하는 실제 테이블 이름이다. 예: export_monthly.',
  period_start DATE NOT NULL COMMENT '이 작업이 처리하려는 기간의 시작일이다.',
  period_end DATE NOT NULL COMMENT '처리 대상 기간의 마지막 날이다. 시작일을 포함한 양끝 포함 범위다.',
  status ENUM('RUNNING','SUCCESS','FAILED') NOT NULL DEFAULT 'RUNNING' COMMENT 'RUNNING 실행 중, SUCCESS 정상 완료, FAILED 실패를 저장한다.',
  started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '이번 실행이 시작된 날짜와 시각을 초까지 저장한다.',
  finished_at DATETIME NULL COMMENT '이번 실행이 종료된 시각이다. 아직 실행 중이면 NULL이다.',
  request_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '이번 실행에서 실제 API를 호출한 횟수다. 재시도 호출도 센다.',
  received_count BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '원천에서 받은 원본 데이터 행 수다. 내부 집계면 읽은 입력 행 수로 정의한다.',
  PRIMARY KEY (run_id),
  FOREIGN KEY (source_code) REFERENCES service_option (option_code),
  KEY ix_batch_source (source_code, started_at),
  CHECK (period_end>=period_start),
  KEY ix_run_target_period (target_table, period_start, period_end, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE fx_daily (
  base_date DATE NOT NULL COMMENT '환율 원천의 기준 영업일이다. 실제 API를 호출한 날짜와 다를 수 있다.',
  currency_code VARCHAR(12) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '이 환율 값의 대상 통화 코드다. currency를 참조한다.',
  rate_krw DECIMAL(20,6) NOT NULL COMMENT 'currency.quote_unit만큼의 외화를 원화로 표시한 매매기준율이다.',
  PRIMARY KEY (base_date, currency_code),
  FOREIGN KEY (currency_code) REFERENCES currency (currency_code),
  CHECK (rate_krw>0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE article (
  article_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '우리 DB 안에서 기사 한 건을 구분하는 자동 번호다.',
  source_code VARCHAR(40) NOT NULL COMMENT '시장뉴스 또는 단신속보를 구분하는 출처 코드다.',
  external_id VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '원천 API가 부여한 기사 ID다. 내부 article_id와 다른 값이다.',
  title VARCHAR(600) NOT NULL COMMENT '원천 기사의 제목이다.',
  published_date DATE NOT NULL COMMENT '원천에 표시된 기사 발행일이다. 수집 실행일이 아니다.',
  source_url VARCHAR(2048) NOT NULL COMMENT '원문 기사 페이지로 이동할 링크다.',
  body_masked LONGTEXT NOT NULL COMMENT '원문을 정제하고 연락처 등 개인정보를 가린 본문이다.',
  category VARCHAR(120) NULL COMMENT '원천이 제공한 기사 분류다. 제공되지 않으면 NULL이다.',
  PRIMARY KEY (article_id),
  UNIQUE KEY uq_article_source_id (source_code, external_id),
  FOREIGN KEY (source_code) REFERENCES service_option (option_code),
  KEY ix_article_date (source_code, published_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE chat_session (
  session_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '대화방 하나를 구분하는 번호다. 개별 메시지 번호와는 다르다.',
  user_id BIGINT UNSIGNED NOT NULL COMMENT '이 대화방을 만든 회원 번호다.',
  title VARCHAR(160) NOT NULL COMMENT '첫 질문 등을 바탕으로 만든 대화 목록용 제목이다.',
  context_json JSON NOT NULL COMMENT '서버가 검증한 마지막 대화 조건과 관심 적용 확인 상태다. LLM이 질문에서 후보를 추출할 수 있다.',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '대화가 마지막으로 갱신된 시각을 초까지 저장한다.',
  PRIMARY KEY (session_id),
  FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
  KEY ix_session_user (user_id, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE report_subscription (
  subscription_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '정기 리포트 구독 한 건의 번호다.',
  user_id BIGINT UNSIGNED NOT NULL COMMENT '구독을 신청한 회원 번호다.',
  period_code VARCHAR(40) NOT NULL COMMENT 'WEEKLY·MONTHLY·QUARTERLY 같은 공용 주기 코드를 참조한다.',
  filters_json JSON NOT NULL COMMENT '구독 대상 HS6·국가·주제 조건을 묶어 저장한다.',
  enabled BOOLEAN NOT NULL DEFAULT TRUE COMMENT '이 개인 구독을 계속 발행할지 저장한다. FALSE면 중지다.',
  PRIMARY KEY (subscription_id),
  FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
  FOREIGN KEY (period_code) REFERENCES service_option (option_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE user_interest (
  interest_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '등록한 관심 항목 한 건의 내부 번호다. 사용자 번호와는 다르다.',
  user_id BIGINT UNSIGNED NOT NULL COMMENT '이 관심을 등록한 회원의 users.user_id다.',
  hs_code VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NULL COMMENT '관심 품목의 HS6 코드다. 국가 관심 행이면 NULL로 둔다.',
  hs_ver VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NULL COMMENT '연결한 hs_code의 사전 판본이다. 코드와 함께 한 쌍으로 저장한다.',
  country_code VARCHAR(12) CHARACTER SET ascii COLLATE ascii_bin NULL COMMENT '관심 국가 코드다. 품목 관심 행이면 NULL로 둔다.',
  PRIMARY KEY (interest_id),
  UNIQUE KEY uq_interest_hs (user_id, hs_code, hs_ver),
  UNIQUE KEY uq_interest_country (user_id, country_code),
  FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
  FOREIGN KEY (hs_code, hs_ver) REFERENCES hs_code (hs_code, hs_ver),
  FOREIGN KEY (country_code) REFERENCES country (country_code),
  CHECK ((hs_code IS NOT NULL AND country_code IS NULL AND CHAR_LENGTH(hs_code)=6) OR (hs_code IS NULL AND country_code IS NOT NULL)),
  CHECK ((hs_code IS NULL AND hs_ver IS NULL) OR (hs_code IS NOT NULL AND hs_ver IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE export_monthly (
  trade_month DATE NOT NULL COMMENT '통계 대상 월을 그 월의 1일로 저장한다. 예를 들어 2025-01-01이다.',
  country_code VARCHAR(12) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '수출 상대 국가 코드다. country의 기준정보를 참조한다.',
  hs10_code VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '관세청에서 받은 수출 품목의 10자리 HS 코드다.',
  hs_ver VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT 'HS10과 자동 생성 HS6가 함께 사용하는 공통 사전 판본. 예: HS2022.',
  hs4_code VARCHAR(4) CHARACTER SET ascii COLLATE ascii_bin GENERATED ALWAYS AS (LEFT(hs10_code,4)) STORED COMMENT 'hs10_code의 앞 4자리를 DB가 자동 계산한 조회용 코드다.',
  hs6_code VARCHAR(6) CHARACTER SET ascii COLLATE ascii_bin GENERATED ALWAYS AS (LEFT(hs10_code,6)) STORED COMMENT 'hs10_code의 앞 6자리를 DB가 자동 계산한 기본 조회 코드다.',
  amount_usd DECIMAL(22,2) NOT NULL COMMENT '해당 월·국가·HS10의 수출 금액을 미국 달러 기준으로 저장한다.',
  weight_kg DECIMAL(22,3) NULL COMMENT '같은 원본 행의 수출 중량을 kg로 저장한다. 미제공이면 NULL이다.',
  history_months TINYINT UNSIGNED NULL COMMENT '일회성 판정의 과거 평균에 사용한 유효 월 수다. 최대 12개월이며 미계산은 NULL이다.',
  baseline_amount_usd DECIMAL(22,2) NULL COMMENT '현재 월을 제외한 직전 최대 12개월의 유효 수출금액 평균이다.',
  median_unit_usd_kg DECIMAL(24,6) NULL COMMENT '유효 과거 월의 금액÷중량 값 중 중앙값이다. 단위는 USD/kg다.',
  anomaly_code ENUM('NONE','R1','R2','R3','R1_R2','R1_R3','R2_R3','R1_R2_R3') NULL COMMENT '일회성 거래 가능성에 해당한 규칙 조합 코드다. NONE은 적용 가능한 규칙에서 해당 없음, NULL은 아직 미계산이다.',
  PRIMARY KEY (trade_month, country_code, hs10_code, hs_ver),
  FOREIGN KEY (country_code) REFERENCES country (country_code),
  FOREIGN KEY (hs10_code, hs_ver) REFERENCES hs_code (hs_code, hs_ver),
  KEY ix_export_monthly_hs6 (hs6_code, hs_ver, trade_month, country_code),
  KEY ix_export_monthly_hs4 (hs4_code, hs_ver, trade_month, country_code),
  KEY ix_export_monthly_country (country_code, trade_month, hs6_code),
  KEY ix_export_monthly_hs10 (hs10_code, hs_ver, trade_month, country_code),
  CHECK (DAYOFMONTH(trade_month)=1 AND CHAR_LENGTH(hs10_code)=10),
  CHECK (amount_usd>=0 AND (weight_kg IS NULL OR weight_kg>=0)),
  CHECK (history_months IS NULL OR history_months<=12),
  CHECK (hs_ver REGEXP '^[A-Z0-9_]+$'),
  FOREIGN KEY (hs6_code, hs_ver) REFERENCES hs_code (hs_code, hs_ver)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE import_monthly (
  trade_month DATE NOT NULL COMMENT '통계 대상 월을 그 월의 1일로 저장한다. 예를 들어 2025-01-01이다.',
  country_code VARCHAR(12) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '수입 상대 국가 코드다. country의 기준정보를 참조한다.',
  hs10_code VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '관세청에서 받은 수입 품목의 10자리 HS 코드다.',
  hs_ver VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT 'HS10과 자동 생성 HS6가 함께 사용하는 공통 사전 판본. 예: HS2022.',
  hs4_code VARCHAR(4) CHARACTER SET ascii COLLATE ascii_bin GENERATED ALWAYS AS (LEFT(hs10_code,4)) STORED COMMENT 'hs10_code의 앞 4자리를 DB가 자동 계산한 조회용 코드다.',
  hs6_code VARCHAR(6) CHARACTER SET ascii COLLATE ascii_bin GENERATED ALWAYS AS (LEFT(hs10_code,6)) STORED COMMENT 'hs10_code의 앞 6자리를 DB가 자동 계산한 기본 조회 코드다.',
  amount_usd DECIMAL(22,2) NOT NULL COMMENT '해당 월·국가·HS10의 수입 금액을 미국 달러 기준으로 저장한다.',
  weight_kg DECIMAL(22,3) NULL COMMENT '같은 원본 행의 수입 중량을 kg로 저장한다. 미제공이면 NULL이다.',
  history_months TINYINT UNSIGNED NULL COMMENT '일회성 판정의 과거 평균에 사용한 유효 월 수다. 최대 12개월이며 미계산은 NULL이다.',
  baseline_amount_usd DECIMAL(22,2) NULL COMMENT '현재 월을 제외한 직전 최대 12개월의 유효 수입금액 평균이다.',
  median_unit_usd_kg DECIMAL(24,6) NULL COMMENT '유효 과거 월의 금액÷중량 값 중 중앙값이다. 단위는 USD/kg다.',
  anomaly_code ENUM('NONE','R1','R2','R3','R1_R2','R1_R3','R2_R3','R1_R2_R3') NULL COMMENT '일회성 거래 가능성에 해당한 규칙 조합 코드다. NONE은 적용 가능한 규칙에서 해당 없음, NULL은 아직 미계산이다.',
  PRIMARY KEY (trade_month, country_code, hs10_code, hs_ver),
  FOREIGN KEY (country_code) REFERENCES country (country_code),
  FOREIGN KEY (hs10_code, hs_ver) REFERENCES hs_code (hs_code, hs_ver),
  KEY ix_import_monthly_hs6 (hs6_code, hs_ver, trade_month, country_code),
  KEY ix_import_monthly_hs4 (hs4_code, hs_ver, trade_month, country_code),
  KEY ix_import_monthly_country (country_code, trade_month, hs6_code),
  KEY ix_import_monthly_hs10 (hs10_code, hs_ver, trade_month, country_code),
  CHECK (DAYOFMONTH(trade_month)=1 AND CHAR_LENGTH(hs10_code)=10),
  CHECK (amount_usd>=0 AND (weight_kg IS NULL OR weight_kg>=0)),
  CHECK (history_months IS NULL OR history_months<=12),
  CHECK (hs_ver REGEXP '^[A-Z0-9_]+$'),
  FOREIGN KEY (hs6_code, hs_ver) REFERENCES hs_code (hs_code, hs_ver)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE export_summary (
  trade_month DATE NOT NULL COMMENT 'HS6 수출 합계의 대상 월이다. 월의 1일로 저장한다.',
  country_code VARCHAR(12) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '이 수출 합계의 상대 국가 코드다.',
  hs6_code VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '합계 대상 6자리 HS 코드다. 공식 HS 사전 행을 참조한다.',
  hs_ver VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '연결한 hs6_code의 사전 판본이다. 코드와 함께 한 쌍으로 저장한다.',
  amount_usd DECIMAL(24,2) NOT NULL COMMENT '같은 월·국가·HS6에 속한 원본 수출 금액의 합계다.',
  PRIMARY KEY (trade_month, country_code, hs6_code, hs_ver),
  FOREIGN KEY (country_code) REFERENCES country (country_code),
  FOREIGN KEY (hs6_code, hs_ver) REFERENCES hs_code (hs_code, hs_ver),
  KEY ix_export_summary_hs (hs6_code, hs_ver, trade_month, country_code),
  KEY ix_export_summary_country (country_code, trade_month, hs6_code, hs_ver),
  CHECK (DAYOFMONTH(trade_month)=1 AND CHAR_LENGTH(hs6_code)=6 AND amount_usd>=0),
  CHECK (hs_ver REGEXP '^[A-Z0-9_]+$')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE import_summary (
  trade_month DATE NOT NULL COMMENT 'HS6 수입 합계의 대상 월이다. 월의 1일로 저장한다.',
  country_code VARCHAR(12) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '이 수입 합계의 상대 국가 코드다.',
  hs6_code VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '합계 대상 6자리 HS 코드다. 공식 HS 사전 행을 참조한다.',
  hs_ver VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '연결한 hs6_code의 사전 판본이다. 코드와 함께 한 쌍으로 저장한다.',
  amount_usd DECIMAL(24,2) NOT NULL COMMENT '같은 월·국가·HS6에 속한 원본 수입 금액의 합계다.',
  PRIMARY KEY (trade_month, country_code, hs6_code, hs_ver),
  FOREIGN KEY (country_code) REFERENCES country (country_code),
  FOREIGN KEY (hs6_code, hs_ver) REFERENCES hs_code (hs_code, hs_ver),
  KEY ix_import_summary_hs (hs6_code, hs_ver, trade_month, country_code),
  KEY ix_import_summary_country (country_code, trade_month, hs6_code, hs_ver),
  CHECK (DAYOFMONTH(trade_month)=1 AND CHAR_LENGTH(hs6_code)=6 AND amount_usd>=0),
  CHECK (hs_ver REGEXP '^[A-Z0-9_]+$')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE article_tag (
  tag_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '기사와 대상 하나의 연결을 식별하는 번호다.',
  article_id BIGINT UNSIGNED NOT NULL COMMENT '이 국가·품목·키워드가 연결된 기사 번호다.',
  country_code VARCHAR(12) CHARACTER SET ascii COLLATE ascii_bin NULL COMMENT '기사와 연결된 국가 코드다. 다른 종류의 태그 행이면 NULL이다.',
  hs_code VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NULL COMMENT '기사 내용과 연결한 검증된 HS 품목 코드다. 다른 태그 종류면 NULL이다.',
  hs_ver VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NULL COMMENT '연결한 hs_code의 사전 판본이다. 코드와 함께 한 쌍으로 저장한다.',
  keyword_text VARCHAR(120) COLLATE utf8mb4_bin NULL COMMENT '원천에서 제공하거나 단신속보 본문에서 추출·정규화한 키워드 하나다. 다른 태그 종류이면 NULL이다.',
  PRIMARY KEY (tag_id),
  UNIQUE KEY uq_tag_country (article_id, country_code),
  UNIQUE KEY uq_tag_hs (article_id, hs_code, hs_ver),
  UNIQUE KEY uq_tag_keyword (article_id, keyword_text),
  FOREIGN KEY (article_id) REFERENCES article (article_id) ON DELETE CASCADE,
  FOREIGN KEY (country_code) REFERENCES country (country_code),
  FOREIGN KEY (hs_code, hs_ver) REFERENCES hs_code (hs_code, hs_ver),
  CHECK ((country_code IS NOT NULL)+(hs_code IS NOT NULL)+(keyword_text IS NOT NULL)=1),
  CHECK (keyword_text IS NULL OR CHAR_LENGTH(TRIM(keyword_text))>0),
  CHECK ((hs_code IS NULL AND hs_ver IS NULL) OR (hs_code IS NOT NULL AND hs_ver IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE news_product_daily (
  metric_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '품목·국가·날짜별 지표 한 행의 자동 번호다.',
  metric_date DATE NOT NULL COMMENT '기사 집계를 계산하는 대상일 T다. 새벽 작업에서는 보통 전일이다.',
  hs_code VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '이 집계가 대상으로 하는 HS4 또는 HS6 코드다.',
  hs_ver VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '연결한 hs_code의 사전 판본이다. 코드와 함께 한 쌍으로 저장한다.',
  country_code VARCHAR(12) CHARACTER SET ascii COLLATE ascii_bin NULL COMMENT '특정 국가 집계면 국가 코드, 국가 조건 없는 전체 집계면 NULL이다.',
  country_key VARCHAR(12) CHARACTER SET ascii COLLATE ascii_bin GENERATED ALWAYS AS (COALESCE(country_code,'*')) STORED COMMENT 'country_code가 있으면 그대로, NULL이면 *로 DB가 자동 만든 키다.',
  day_count INT UNSIGNED NOT NULL COMMENT '대상일 T에 발행된 시장뉴스와 단신속보의 고유 기사 건수다. 단어 등장 횟수나 태그 행 수가 아니다.',
  PRIMARY KEY (metric_id),
  UNIQUE KEY uq_news_product (metric_date, hs_code, hs_ver, country_key),
  FOREIGN KEY (hs_code, hs_ver) REFERENCES hs_code (hs_code, hs_ver),
  FOREIGN KEY (country_code) REFERENCES country (country_code),
  CHECK (hs_ver REGEXP '^[A-Z0-9_]+$')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE news_keyword_daily (
  metric_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '국가·키워드·날짜별 지표 행의 자동 번호다.',
  metric_date DATE NOT NULL COMMENT '기사 집계를 계산하는 대상일 T다. 새벽 작업에서는 보통 전일이다.',
  keyword_text VARCHAR(120) COLLATE utf8mb4_bin NOT NULL COMMENT '집계 대상인 정규화된 KOTRA 기사 키워드다.',
  country_code VARCHAR(12) CHARACTER SET ascii COLLATE ascii_bin NULL COMMENT '특정 국가 집계면 국가 코드, 전체 국가 집계면 NULL이다.',
  country_key VARCHAR(12) CHARACTER SET ascii COLLATE ascii_bin GENERATED ALWAYS AS (COALESCE(country_code,'*')) STORED COMMENT '국가 코드가 있으면 그대로, NULL이면 *로 자동 계산한다.',
  day_count INT UNSIGNED NOT NULL COMMENT '대상일 T에 발행된 시장뉴스와 단신속보의 고유 기사 건수다. 단어 등장 횟수나 태그 행 수가 아니다.',
  PRIMARY KEY (metric_id),
  UNIQUE KEY uq_news_keyword (metric_date, keyword_text, country_key),
  FOREIGN KEY (country_code) REFERENCES country (country_code),
  CHECK (CHAR_LENGTH(TRIM(keyword_text))>0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE chat_message (
  message_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '질문 또는 답변 한 건의 자동 번호다.',
  session_id BIGINT UNSIGNED NOT NULL COMMENT '이 메시지가 속한 대화방 번호다.',
  role ENUM('USER','ASSISTANT') NOT NULL COMMENT 'USER는 사용자 질문, ASSISTANT는 에이전트 답변이다.',
  content_masked LONGTEXT NOT NULL COMMENT '전화·이메일 등 개인정보를 마스킹한 질문 또는 답변 본문이다.',
  intent_json JSON NULL COMMENT '답변 당시 서버가 확정한 분석 의도·HS·국가·기간·수출입 조건이다.',
  result_json JSON NULL COMMENT '답변의 결과표·SQL·단위·기사 근거·검증 결과를 묶어 저장한다. 질문 행은 NULL 가능하다.',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '질문 또는 답변을 저장한 시각이다. 초 미만은 저장하지 않는다.',
  PRIMARY KEY (message_id),
  FOREIGN KEY (session_id) REFERENCES chat_session (session_id) ON DELETE CASCADE,
  KEY ix_message_session (session_id, message_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE report (
  report_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '생성할 또는 생성한 리포트 한 건의 번호다.',
  user_id BIGINT UNSIGNED NOT NULL COMMENT '리포트 소유 회원 번호다.',
  subscription_id BIGINT UNSIGNED NULL COMMENT '구독 발행이면 원래 구독 번호다. 요청형이면 NULL이다.',
  report_type VARCHAR(40) NOT NULL COMMENT 'REPORT_TYPE 종류의 공용 옵션 코드다. 예: RPT_MONTHLY.',
  generation_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '동일 발행 작업을 식별하는 64자리 해시다. 비밀번호나 암호키가 아니다.',
  conditions_json JSON NOT NULL COMMENT '이번 발행의 품목·국가·기간·비교기간·선택 섹션을 저장한다.',
  status ENUM('QUEUED','RUNNING','DELAYED','READY','FAILED') NOT NULL DEFAULT 'QUEUED' COMMENT 'QUEUED 대기, RUNNING 생성 중, DELAYED 지연, READY 완료, FAILED 실패다.',
  retry_count SMALLINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '최초 시도를 제외하고 이 리포트 작업을 다시 실행한 누적 횟수다.',
  content_json JSON NULL COMMENT '완성한 섹션·결과표·SQL·기사 근거·검증을 담은 출력 묶음이다. 미완성은 NULL 가능하다.',
  pdf_uri VARCHAR(1024) NULL COMMENT '완료 PDF 파일의 저장 위치다. DB에 PDF 파일 자체를 넣는 값은 아니다.',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '리포트 생성 작업이 등록된 시각이다.',
  completed_at DATETIME NULL COMMENT '리포트가 정상 완료된 시각이다. 대기·진행·실패 상태에서는 NULL 가능하다.',
  PRIMARY KEY (report_id),
  UNIQUE KEY uq_report_generation (generation_key),
  FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
  FOREIGN KEY (subscription_id) REFERENCES report_subscription (subscription_id) ON DELETE SET NULL,
  KEY ix_report_user (user_id, created_at),
  CHECK (status<>'READY' OR (content_json IS NOT NULL AND pdf_uri IS NOT NULL AND completed_at IS NOT NULL)),
  FOREIGN KEY (report_type) REFERENCES service_option (option_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE alert_event (
  event_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '발생한 알림 사건 한 건의 내부 번호다.',
  event_type ENUM('PRODUCT_SPIKE','KEYWORD_SPIKE','MAJOR_ISSUE','REPORT') NOT NULL COMMENT 'PRODUCT_SPIKE 품목 기사 급증, KEYWORD_SPIKE 키워드 기사 급증, MAJOR_ISSUE 주요 이슈, REPORT 리포트 발행이다.',
  article_id BIGINT UNSIGNED NULL COMMENT '기사 관련 알림 사건의 대표 기사 번호다. 대표 기사가 없으면 NULL이다.',
  report_id BIGINT UNSIGNED NULL COMMENT 'REPORT 사건이 알리는 발행 리포트 번호다. 뉴스 사건이면 NULL이다.',
  event_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '사건 종류·대상·날짜 등으로 만든 64자리 중복 방지 해시다.',
  payload_json JSON NOT NULL COMMENT '판정 배수·비교 기간·관련 기사 ID·대상 범위·이동 조건 등의 상세 내용이다.',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '시스템에서 이 사건을 등록한 시각이다.',
  PRIMARY KEY (event_id),
  UNIQUE KEY uq_event_key (event_key),
  FOREIGN KEY (article_id) REFERENCES article (article_id),
  FOREIGN KEY (report_id) REFERENCES report (report_id) ON DELETE CASCADE,
  CHECK ((event_type='REPORT' AND report_id IS NOT NULL AND article_id IS NULL) OR (event_type IN ('PRODUCT_SPIKE','KEYWORD_SPIKE','MAJOR_ISSUE') AND report_id IS NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE user_alert (
  user_alert_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '특정 사용자의 알림함 항목 한 건을 구분하는 번호다.',
  user_id BIGINT UNSIGNED NOT NULL COMMENT '이 알림함 항목을 받는 회원 번호다.',
  event_id BIGINT UNSIGNED NOT NULL COMMENT '알림의 원인이 된 alert_event 번호다.',
  is_interest_match BOOLEAN NOT NULL DEFAULT FALSE COMMENT '편입 당시 이 사건이 내 관심에 맞았는지 저장한다.',
  read_at DATETIME NULL COMMENT '읽은 시각이다. 아직 읽지 않았으면 NULL이다.',
  popup_shown_at DATETIME NULL COMMENT '실제로 팝업이 표시됐다고 확인한 시각이다. 미표시이면 NULL이다.',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '개인 알림함에 편입된 시각이다. 공용 사건 발생 시각과 다를 수 있다.',
  PRIMARY KEY (user_alert_id),
  UNIQUE KEY uq_user_event (user_id, event_id),
  FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
  FOREIGN KEY (event_id) REFERENCES alert_event (event_id) ON DELETE CASCADE,
  KEY ix_user_unread (user_id, read_at, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE VIEW v_export_country_month AS SELECT s.trade_month,s.country_code,SUM(s.amount_usd) AS amount_usd FROM export_summary s JOIN country c ON c.country_code=s.country_code WHERE c.country_role='COUNTRY' GROUP BY s.trade_month,s.country_code;

CREATE VIEW v_export_product_year AS SELECT YEAR(s.trade_month) AS trade_year,s.hs6_code,s.hs_ver,s.country_code,SUM(s.amount_usd) AS amount_usd FROM export_summary s JOIN country c ON c.country_code=s.country_code WHERE c.country_role='COUNTRY' GROUP BY YEAR(s.trade_month),s.hs6_code,s.hs_ver,s.country_code;

CREATE VIEW v_export_region_month AS SELECT s.trade_month,c.region_id,SUM(s.amount_usd) AS amount_usd FROM export_summary s JOIN country c ON c.country_code=s.country_code WHERE c.country_role='COUNTRY' AND c.region_id IS NOT NULL GROUP BY s.trade_month,c.region_id;

CREATE VIEW v_export_continent_month AS SELECT v.trade_month,r.parent_id AS continent_id,SUM(v.amount_usd) AS amount_usd FROM v_export_region_month v JOIN market_region r ON r.region_id=v.region_id GROUP BY v.trade_month,r.parent_id;

CREATE VIEW v_import_country_month AS SELECT s.trade_month,s.country_code,SUM(s.amount_usd) AS amount_usd FROM import_summary s JOIN country c ON c.country_code=s.country_code WHERE c.country_role='COUNTRY' GROUP BY s.trade_month,s.country_code;

CREATE VIEW v_import_product_year AS SELECT YEAR(s.trade_month) AS trade_year,s.hs6_code,s.hs_ver,s.country_code,SUM(s.amount_usd) AS amount_usd FROM import_summary s JOIN country c ON c.country_code=s.country_code WHERE c.country_role='COUNTRY' GROUP BY YEAR(s.trade_month),s.hs6_code,s.hs_ver,s.country_code;

CREATE VIEW v_import_region_month AS SELECT s.trade_month,c.region_id,SUM(s.amount_usd) AS amount_usd FROM import_summary s JOIN country c ON c.country_code=s.country_code WHERE c.country_role='COUNTRY' AND c.region_id IS NOT NULL GROUP BY s.trade_month,c.region_id;

CREATE VIEW v_import_continent_month AS SELECT v.trade_month,r.parent_id AS continent_id,SUM(v.amount_usd) AS amount_usd FROM v_import_region_month v JOIN market_region r ON r.region_id=v.region_id GROUP BY v.trade_month,r.parent_id;

CREATE VIEW v_article_country_day AS SELECT a.published_date,t.country_code,COUNT(DISTINCT a.article_id) AS article_count FROM article a JOIN article_tag t ON t.article_id=a.article_id WHERE a.source_code='KOTRA_MARKET' AND t.country_code IS NOT NULL GROUP BY a.published_date,t.country_code;

CREATE VIEW v_article_day AS SELECT published_date,COUNT(DISTINCT article_id) AS article_count FROM article WHERE source_code='KOTRA_MARKET' GROUP BY published_date;

CREATE VIEW v_country_cluster AS SELECT m.model_run_id,m.train_start,m.train_end,m.model_name,m.created_at,j.country_code,j.growth_class,j.growth_rate,j.amount_usd,j.volatility FROM model_run m JOIN JSON_TABLE(m.country_results_json,'$[*]' COLUMNS(country_code VARCHAR(12) PATH '$.country_code',growth_class VARCHAR(20) PATH '$.growth_class',growth_rate DECIMAL(18,6) PATH '$.growth_rate' NULL ON EMPTY,amount_usd DECIMAL(24,2) PATH '$.amount_usd',volatility DECIMAL(18,6) PATH '$.volatility' NULL ON EMPTY)) AS j WHERE m.status='SUCCESS';
