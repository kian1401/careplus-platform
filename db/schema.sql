-- =====================================================================
--  سرویسا (Care+) — اسکیمای پایگاه داده
--  PostgreSQL 16 + PostGIS 3 + pgcrypto  |  نسخه ۱.۰
--  مبنا: بخش ۸ سند جامع (مدل داده، ۶۲ جدول)
--
--  قواعد:
--   • کلید اصلی UUIDv7 (مرتب بر پایه زمان) — روی PG16 با تابع uuid_v7() شبیه‌سازی شده
--     (روی PostgreSQL 18+ می‌توانید به تابع بومی uuidv7() سوئیچ کنید)
--   • مبالغ: BIGINT در واحد خرد (ریال) + ستون currency
--   • زمان: timestamptz (UTC)
--   • حذف نرم: deleted_at ؛ حسابرسی: platform.audit_log (پارتیشن ماهانه)
--   • هر سرویس اسکیمای مستقل خود را دارد (فاز ۱: یک کلاستر، فاز ۳: کلاستر جدا)
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS postgis;      -- انواع جغرافیایی و جست‌وجوی مکانی
CREATE EXTENSION IF NOT EXISTS pgcrypto;     -- gen_random_uuid
CREATE EXTENSION IF NOT EXISTS btree_gist;   -- قید EXCLUDE روی booking (uuid + tstzrange)
CREATE EXTENSION IF NOT EXISTS citext;       -- ایمیل بدون حساسیت به بزرگی/کوچکی
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- جست‌وجوی شباهت روی کد سفارش

CREATE SCHEMA IF NOT EXISTS platform;
CREATE SCHEMA IF NOT EXISTS identity;
CREATE SCHEMA IF NOT EXISTS catalog;
CREATE SCHEMA IF NOT EXISTS ord;         -- سرویس سفارش (نام جدول order کلمه رزرو است و با "")
CREATE SCHEMA IF NOT EXISTS dispatch;
CREATE SCHEMA IF NOT EXISTS pay;
CREATE SCHEMA IF NOT EXISTS trust;
CREATE SCHEMA IF NOT EXISTS risk;
CREATE SCHEMA IF NOT EXISTS comms;
CREATE SCHEMA IF NOT EXISTS ai;
CREATE SCHEMA IF NOT EXISTS analytics;

-- ---------------------------------------------------------------- helpers
CREATE OR REPLACE FUNCTION platform.uuid_v7() RETURNS uuid
LANGUAGE plpgsql VOLATILE AS $$
DECLARE
  ms_hex text := lpad(to_hex((extract(epoch FROM clock_timestamp())*1000)::bigint), 12, '0');
  r      text := replace(gen_random_uuid()::text, '-', '');
BEGIN
  RETURN (substr(ms_hex,1,8) || '-' || substr(ms_hex,9,4) || '-7' || substr(r,1,3)
          || '-8' || substr(r,4,3) || '-' || substr(r,7,12))::uuid;
END $$;

CREATE OR REPLACE FUNCTION platform.touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at := now(); RETURN NEW; END $$;

-- یک تریگر عمومی برای هر جدولی که updated_at دارد (نمونهٔ ثبت در انتهای فایل)

-- =====================================================================
-- ۱) IDENTITY — هویت، ورود، نقش‌ها، حساب سازمانی (۱۱ جدول)
-- =====================================================================
CREATE TABLE identity."user" (
  id                 uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  phone_e164         text UNIQUE NOT NULL,                 -- رمزنگاری‌شده در لایه اپ/ستون جدا
  phone_hash         text UNIQUE NOT NULL,                 -- برای جست‌وجوی سریع و بدون افشا
  phone_verified_at  timestamptz,
  email              citext,
  national_id_hash   text,
  status             text NOT NULL DEFAULT 'ACTIVE'
                     CHECK (status IN ('ACTIVE','SUSPENDED','DELETED','PENDING')),
  locale             text NOT NULL DEFAULT 'fa-IR',
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  deleted_at         timestamptz
);

CREATE TABLE identity.user_profile (
  user_id     uuid PRIMARY KEY REFERENCES identity."user"(id) ON DELETE CASCADE,
  full_name   text,
  avatar_url  text,
  gender      text,
  birth_date  date,
  addresses   jsonb NOT NULL DEFAULT '[]'::jsonb,
  preferences jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE identity.auth_credential (
  id           uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  user_id      uuid NOT NULL REFERENCES identity."user"(id) ON DELETE CASCADE,
  type         text NOT NULL CHECK (type IN ('OTP','OAUTH_GOOGLE','OAUTH_APPLE','PASSKEY','PASSWORD')),
  secret_hash  text,
  public_key   text,                       -- Passkey (WebAuthn)
  credential_id text,                      -- WebAuthn credential id
  metadata     jsonb NOT NULL DEFAULT '{}'::jsonb,
  last_used_at timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, type, credential_id)
);

CREATE TABLE identity.auth_session (
  id                 uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  user_id            uuid NOT NULL REFERENCES identity."user"(id) ON DELETE CASCADE,
  device_id          uuid,
  refresh_token_hash text NOT NULL,
  issued_at          timestamptz NOT NULL DEFAULT now(),
  expires_at         timestamptz NOT NULL,
  revoked_at         timestamptz,
  rotated_from       uuid REFERENCES identity.auth_session(id),
  ip                 inet,
  user_agent         text,
  risk_score         numeric(5,2)
);
CREATE INDEX idx_session_user_alive ON identity.auth_session (user_id) WHERE revoked_at IS NULL;

CREATE TABLE identity.otp_challenge (
  id                 uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  phone_hash         text NOT NULL,
  code_hash          text NOT NULL,
  purpose            text NOT NULL CHECK (purpose IN ('LOGIN','VERIFY_PHONE','CHANGE_PHONE','PAYOUT_CONFIRM')),
  attempts           smallint NOT NULL DEFAULT 0,
  expires_at         timestamptz NOT NULL,
  consumed_at        timestamptz,
  ip                 inet,
  device_fingerprint text,
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_otp_phone_time ON identity.otp_challenge (phone_hash, created_at DESC);

CREATE TABLE identity.device (
  id          uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  user_id     uuid NOT NULL REFERENCES identity."user"(id) ON DELETE CASCADE,
  platform    text NOT NULL CHECK (platform IN ('ANDROID','IOS','WEB')),
  model       text, os_version text, app_version text,
  push_token  text,
  fingerprint text,
  trusted     boolean NOT NULL DEFAULT false,
  created_at  timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz
);

CREATE TABLE identity.role (
  code        text PRIMARY KEY,
  title_fa    text NOT NULL,
  description text
);

CREATE TABLE identity.permission (
  code        text PRIMARY KEY,            -- resource:action:scope  مثل order:refund:region
  resource    text NOT NULL,
  action      text NOT NULL,
  scope       text NOT NULL DEFAULT 'self'
);

CREATE TABLE identity.role_permission (
  role_code       text REFERENCES identity.role(code) ON DELETE CASCADE,
  permission_code text REFERENCES identity.permission(code) ON DELETE CASCADE,
  PRIMARY KEY (role_code, permission_code)
);

CREATE TABLE identity.user_role (
  user_id    uuid REFERENCES identity."user"(id) ON DELETE CASCADE,
  role_code  text REFERENCES identity.role(code) ON DELETE CASCADE,
  scope_type text NOT NULL DEFAULT 'GLOBAL' CHECK (scope_type IN ('GLOBAL','REGION','ORG')),
  scope_id   uuid,
  granted_by uuid, granted_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, role_code, scope_type, scope_id)
);

CREATE TABLE identity.org_account (
  id                    uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  legal_name            text NOT NULL,
  national_entity_id    text UNIQUE NOT NULL,
  economic_code         text,
  vat_registered        boolean NOT NULL DEFAULT true,
  address               text,
  credit_limit          bigint NOT NULL DEFAULT 0,
  payment_terms_days    smallint NOT NULL DEFAULT 0,
  rate_card_id          uuid,
  contract_file_id      uuid,
  status                text NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN ('PENDING','ACTIVE','SUSPENDED','CLOSED')),
  created_at            timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE identity.org_member (
  org_id         uuid REFERENCES identity.org_account(id) ON DELETE CASCADE,
  user_id        uuid REFERENCES identity."user"(id) ON DELETE CASCADE,
  org_role       text NOT NULL CHECK (org_role IN ('OWNER','ADMIN','FINANCE','REQUESTER','APPROVER')),
  cost_center    text,
  approval_limit bigint NOT NULL DEFAULT 0,
  PRIMARY KEY (org_id, user_id, org_role)
);

CREATE TABLE identity.consent_record (
  id          uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  user_id     uuid NOT NULL REFERENCES identity."user"(id) ON DELETE CASCADE,
  consent_type text NOT NULL CHECK (consent_type IN
              ('LOCATION_TRACKING','CALL_RECORDING','VIDEO_RECORDING','MODEL_TRAINING','MARKETING')),
  version     text NOT NULL,
  granted_at  timestamptz NOT NULL DEFAULT now(),
  revoked_at  timestamptz,
  ip          inet
);

-- =====================================================================
-- ۲) PROVIDER — متخصص، مدارک، احراز، صلاحیت، ظرفیت (۱۲ جدول)
-- =====================================================================
CREATE TABLE identity.provider_profile (
  user_id            uuid PRIMARY KEY REFERENCES identity."user"(id) ON DELETE CASCADE,
  display_name       text NOT NULL,
  bio                text,
  verified_level     smallint NOT NULL DEFAULT 0 CHECK (verified_level BETWEEN 0 AND 4),
  status             text NOT NULL DEFAULT 'PENDING'
                     CHECK (status IN ('PENDING','ACTIVE','SUSPENDED','BANNED','DORMANT')),
  tier               text NOT NULL DEFAULT 'BASE' CHECK (tier IN ('BASE','PRO','ELITE','ORG')),
  rating_avg         numeric(3,2) NOT NULL DEFAULT 0,
  rating_count       integer NOT NULL DEFAULT 0,
  acceptance_rate    numeric(4,3) NOT NULL DEFAULT 0,
  on_time_rate       numeric(4,3) NOT NULL DEFAULT 0,
  cancel_rate        numeric(4,3) NOT NULL DEFAULT 0,
  capacity_per_day   smallint NOT NULL DEFAULT 3,
  last_location      geography(Point,4326),
  last_location_at   timestamptz,
  blocked_until      timestamptz,
  commission_rate    numeric(4,3) NOT NULL DEFAULT 0.150,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_provider_loc_active ON identity.provider_profile USING gist (last_location)
  WHERE status = 'ACTIVE' AND blocked_until IS NULL;
CREATE INDEX idx_provider_tier ON identity.provider_profile (tier, verified_level);

CREATE TABLE identity.provider_org_link (
  provider_id    uuid REFERENCES identity.provider_profile(user_id) ON DELETE CASCADE,
  org_id         uuid REFERENCES identity.org_account(id) ON DELETE CASCADE,
  employment_type text NOT NULL DEFAULT 'CONTRACT' CHECK (employment_type IN ('EMPLOYEE','CONTRACT')),
  revenue_share  numeric(4,3) NOT NULL DEFAULT 0.700,
  PRIMARY KEY (provider_id, org_id)
);

CREATE TABLE identity.provider_document (
  id                uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  provider_id       uuid NOT NULL REFERENCES identity.provider_profile(user_id) ON DELETE CASCADE,
  kind              text NOT NULL CHECK (kind IN
                    ('NATIONAL_ID','CRIMINAL_RECORD','SKILL_CERT','INSURANCE','BUSINESS_LICENSE','OTHER')),
  file_id           uuid NOT NULL,
  ocr_data          jsonb NOT NULL DEFAULT '{}'::jsonb,
  issue_date        date,
  expiry_date       date,
  verification_status text NOT NULL DEFAULT 'PENDING'
                    CHECK (verification_status IN ('PENDING','VERIFIED','REJECTED','EXPIRED')),
  verified_by       uuid, verification_notes text,
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_doc_expiry ON identity.provider_document (expiry_date)
  WHERE verification_status = 'VERIFIED';

CREATE TABLE identity.identity_verification (
  id                uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  provider_id       uuid NOT NULL REFERENCES identity.provider_profile(user_id) ON DELETE CASCADE,
  selfie_file_id    uuid NOT NULL,
  doc_file_id       uuid,
  face_match_score  numeric(5,2),
  liveness_score    numeric(5,2),
  tamper_score      numeric(5,2),
  decision          text NOT NULL CHECK (decision IN ('APPROVED','REJECTED','MANUAL_REVIEW')),
  decided_by        text NOT NULL DEFAULT 'AUTO' CHECK (decided_by IN ('AUTO','HUMAN')),
  decided_by_user   uuid,
  decided_at        timestamptz NOT NULL DEFAULT now(),
  notes             text
);

CREATE TABLE identity.skill_test_attempt (
  id           uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  provider_id  uuid NOT NULL REFERENCES identity.provider_profile(user_id) ON DELETE CASCADE,
  category_id  uuid,
  score        smallint,
  passed       boolean,
  video_file_id uuid,
  reviewed_by  uuid,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE identity.provider_skill (
  provider_id      uuid REFERENCES identity.provider_profile(user_id) ON DELETE CASCADE,
  skill_code       text NOT NULL,
  level            smallint NOT NULL DEFAULT 1 CHECK (level BETWEEN 1 AND 5),
  verified_at      timestamptz,
  experience_years smallint,
  PRIMARY KEY (provider_id, skill_code)
);

CREATE TABLE identity.provider_equipment (
  provider_id   uuid REFERENCES identity.provider_profile(user_id) ON DELETE CASCADE,
  equipment_code text NOT NULL,
  quantity      smallint NOT NULL DEFAULT 1,
  verified_at   timestamptz,
  photo_file_id uuid,
  PRIMARY KEY (provider_id, equipment_code)
);

CREATE TABLE identity.provider_service_area (
  id           uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  provider_id  uuid NOT NULL REFERENCES identity.provider_profile(user_id) ON DELETE CASCADE,
  zone_id      uuid,
  polygon      geography(Polygon,4326),
  radius_km    numeric(5,2),
  priority     smallint NOT NULL DEFAULT 1,
  active       boolean NOT NULL DEFAULT true
);
CREATE INDEX idx_psa_provider ON identity.provider_service_area (provider_id) WHERE active;
CREATE INDEX idx_psa_polygon ON identity.provider_service_area USING gist (polygon);

CREATE TABLE identity.provider_availability (
  id           uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  provider_id  uuid NOT NULL REFERENCES identity.provider_profile(user_id) ON DELETE CASCADE,
  weekday      smallint CHECK (weekday BETWEEN 0 AND 6),
  date_override date,
  from_time    time NOT NULL,
  to_time      time NOT NULL,
  is_available boolean NOT NULL DEFAULT true,
  CHECK (from_time < to_time)
);

CREATE TABLE identity.provider_capacity (
  provider_id      uuid REFERENCES identity.provider_profile(user_id) ON DELETE CASCADE,
  date             date NOT NULL,
  slots_total      smallint NOT NULL DEFAULT 3,
  accepted_count   smallint NOT NULL DEFAULT 0,
  active_order_id  uuid,
  PRIMARY KEY (provider_id, date),
  CHECK (accepted_count <= slots_total)
);

CREATE TABLE identity.provider_sanction (
  id           uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  provider_id  uuid NOT NULL REFERENCES identity.provider_profile(user_id) ON DELETE CASCADE,
  type         text NOT NULL CHECK (type IN ('WARN','SUSPEND','BAN','PENALTY','DELIST')),
  reason_code  text NOT NULL,
  reason_text  text,
  from_date    timestamptz NOT NULL DEFAULT now(),
  to_date      timestamptz,
  amount       bigint,
  applied_by   uuid NOT NULL,
  appeal_status text NOT NULL DEFAULT 'NONE' CHECK (appeal_status IN ('NONE','FILED','UPHELD','REVERSED')),
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE identity.provider_tier_history (
  id              uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  provider_id     uuid NOT NULL REFERENCES identity.provider_profile(user_id) ON DELETE CASCADE,
  tier            text NOT NULL,
  commission_rate numeric(4,3) NOT NULL,
  from_date       timestamptz NOT NULL DEFAULT now(),
  to_date         timestamptz,
  reason          text
);

-- =====================================================================
-- ۳) CATALOG & PRICING — دسته‌بندی، خدمت، تعرفه، قواعد، تخمین (۹ جدول)
-- =====================================================================
CREATE TABLE catalog.service_category (
  id                 uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  slug               text UNIQUE NOT NULL,
  title_fa           text NOT NULL,
  parent_id          uuid REFERENCES catalog.service_category(id),
  icon               text,
  seo_meta           jsonb NOT NULL DEFAULT '{}'::jsonb,
  warranty_days_default smallint NOT NULL DEFAULT 0,
  insurance_required boolean NOT NULL DEFAULT true,
  active             boolean NOT NULL DEFAULT true
);

CREATE TABLE catalog.service_item (
  id                 uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  category_id        uuid NOT NULL REFERENCES catalog.service_category(id),
  slug               text UNIQUE NOT NULL,
  title_fa           text NOT NULL,
  pricing_model      text NOT NULL CHECK (pricing_model IN
                     ('FIXED','BAND','INSPECTION','BID','HOURLY','RETAINER')),
  unit               text NOT NULL DEFAULT 'FIXED' CHECK (unit IN ('FIXED','MANUAL','HOURLY','SQ_METER')),
  duration_estimate_min smallint,
  required_skills    text[] NOT NULL DEFAULT '{}',
  required_equipment text[] NOT NULL DEFAULT '{}',
  triage_flow_id     uuid,
  active             boolean NOT NULL DEFAULT true
);

CREATE TABLE catalog.rate_card (
  id             uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  version        text NOT NULL,
  region_id      uuid,
  org_id         uuid REFERENCES identity.org_account(id),
  effective_from timestamptz NOT NULL DEFAULT now(),
  effective_to   timestamptz,
  active         boolean NOT NULL DEFAULT true,
  created_by     uuid,
  UNIQUE (version, region_id, org_id)
);

CREATE TABLE catalog.rate_card_item (
  id              uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  rate_card_id    uuid NOT NULL REFERENCES catalog.rate_card(id) ON DELETE CASCADE,
  service_item_id uuid NOT NULL REFERENCES catalog.service_item(id),
  base_price      bigint NOT NULL,
  min_price       bigint,
  max_price       bigint,
  travel_fee_rule jsonb NOT NULL DEFAULT '{}'::jsonb,
  materials_included boolean NOT NULL DEFAULT false,
  UNIQUE (rate_card_id, service_item_id)
);

CREATE TABLE catalog.price_rule (
  id           uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  name         text NOT NULL,
  kind         text NOT NULL CHECK (kind IN ('SURGE','COMPLEXITY','URGENCY','DISCOUNT','TRAVEL','MINIMUM')),
  scope        jsonb NOT NULL DEFAULT '{}'::jsonb,   -- منطقه، دسته، ساعت، سازمان
  formula      jsonb NOT NULL,
  priority     smallint NOT NULL DEFAULT 100,
  valid_from   timestamptz NOT NULL DEFAULT now(),
  valid_to     timestamptz,
  active       boolean NOT NULL DEFAULT true
);

CREATE TABLE catalog.demand_supply_metric (
  zone_id          uuid NOT NULL,
  window_start     timestamptz NOT NULL,
  orders_count     integer NOT NULL DEFAULT 0,
  active_providers integer NOT NULL DEFAULT 0,
  demand_index     numeric(8,3),
  surge_factor     numeric(4,3) NOT NULL DEFAULT 1.000 CHECK (surge_factor BETWEEN 0.950 AND 1.500),
  PRIMARY KEY (zone_id, window_start)
);

CREATE TABLE catalog.price_quote (
  id                 uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  order_id           uuid,
  service_item_id    uuid NOT NULL REFERENCES catalog.service_item(id),
  model              text NOT NULL,
  min_amount         bigint NOT NULL,
  max_amount         bigint NOT NULL,
  final_amount       bigint,
  currency           char(3) NOT NULL DEFAULT 'IRR',
  breakdown          jsonb NOT NULL DEFAULT '[]'::jsonb,
  engine_version     text NOT NULL,
  rate_card_version  text NOT NULL,
  confidence         numeric(4,3),
  expires_at         timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  CHECK (min_amount <= max_amount)
);

CREATE TABLE catalog.price_quote_revision (
  id                    uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  quote_id              uuid NOT NULL REFERENCES catalog.price_quote(id) ON DELETE CASCADE,
  revised_by            uuid NOT NULL,
  reason_code           text NOT NULL,
  old_amount            bigint NOT NULL,
  new_amount            bigint NOT NULL,
  media_file_id         uuid,
  approved_by_customer  boolean NOT NULL DEFAULT false,
  approved_at           timestamptz,
  created_at            timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE catalog.promotion (
  id             uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  code           text UNIQUE,
  kind           text NOT NULL CHECK (kind IN ('PERCENT','AMOUNT','FREE_ADDON','FREE_TRAVEL')),
  value          numeric(12,2) NOT NULL,
  scope          jsonb NOT NULL DEFAULT '{}'::jsonb,
  max_uses       integer, per_user_limit smallint NOT NULL DEFAULT 1,
  min_basket     bigint NOT NULL DEFAULT 0,
  valid_from     timestamptz NOT NULL DEFAULT now(),
  valid_to       timestamptz,
  applies_to_org boolean NOT NULL DEFAULT false,
  active         boolean NOT NULL DEFAULT true
);

CREATE TABLE catalog.tax_rule (
  id               uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  region_id        uuid,
  category_id      uuid REFERENCES catalog.service_category(id),
  vat_rate         numeric(5,4) NOT NULL DEFAULT 0.1000,
  withholding_rate numeric(5,4) NOT NULL DEFAULT 0.0000,
  effective_from   timestamptz NOT NULL DEFAULT now(),
  effective_to     timestamptz,
  active           boolean NOT NULL DEFAULT true
);

-- =====================================================================
-- ۴) ORDER — سفارش، رسانه، تریاژ، اقلام، چک‌لیست، رزرو (۱۲ جدول)
-- =====================================================================
CREATE TABLE ord."order" (
  id                   uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  code                 text UNIQUE NOT NULL,
  customer_id          uuid NOT NULL REFERENCES identity."user"(id),
  org_id               uuid REFERENCES identity.org_account(id),
  service_item_id      uuid NOT NULL REFERENCES catalog.service_item(id),
  category_path        text[] NOT NULL DEFAULT '{}',
  status               text NOT NULL DEFAULT 'CREATED' CHECK (status IN
                       ('CREATED','DISPATCHING','ASSIGNED','EN_ROUTE','IN_PROGRESS','COMPLETED',
                        'QUALITY_WINDOW','CLOSED','CANCELLED','DISPUTED','EXPIRED')),
  urgency              text NOT NULL DEFAULT 'SCHEDULED' CHECK (urgency IN ('EMERGENCY','SAME_DAY','SCHEDULED')),
  severity             smallint CHECK (severity BETWEEN 1 AND 5),
  skills_required      text[] NOT NULL DEFAULT '{}',
  equipment_required   text[] NOT NULL DEFAULT '{}',
  location             geography(Point,4326) NOT NULL,
  address_id           uuid,
  address_snapshot     jsonb NOT NULL DEFAULT '{}'::jsonb,
  slot_start           timestamptz,
  slot_end             timestamptz,
  price_quote_id       uuid REFERENCES catalog.price_quote(id),
  final_amount         bigint,
  currency             char(3) NOT NULL DEFAULT 'IRR',
  payment_state        text NOT NULL DEFAULT 'UNPAID' CHECK (payment_state IN
                       ('UNPAID','AUTHORIZED','PARTIALLY_PAID','PAID','REFUNDED','PARTIALLY_REFUNDED')),
  escrow_state         text NOT NULL DEFAULT 'PENDING_PAYMENT' CHECK (escrow_state IN
                       ('PENDING_PAYMENT','HELD_IN_ESCROW','PARTIALLY_RELEASED','RELEASED_TO_PROVIDER',
                        'SETTLED','DISPUTED','FROZEN','REFUNDED','PARTIALLY_REFUNDED')),
  assigned_provider_id uuid REFERENCES identity.provider_profile(user_id),
  warranty_expires_at  timestamptz,
  source               text NOT NULL DEFAULT 'APP' CHECK (source IN ('APP','WEB','CALL_CENTER','API','RECURRING')),
  requires_human_triage boolean NOT NULL DEFAULT false,
  risk_band            text CHECK (risk_band IN ('LOW','MEDIUM','HIGH')),
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  completed_at         timestamptz,
  deleted_at           timestamptz,
  CHECK (slot_end IS NULL OR slot_start IS NULL OR slot_start < slot_end)
);
CREATE INDEX idx_order_customer ON ord."order" (customer_id, created_at DESC);
CREATE INDEX idx_order_provider ON ord."order" (assigned_provider_id, created_at DESC);
CREATE INDEX idx_order_loc ON ord."order" USING gist (location);
CREATE INDEX idx_order_open ON ord."order" (status, urgency, slot_start)
  WHERE status IN ('CREATED','DISPATCHING','ASSIGNED','EN_ROUTE','IN_PROGRESS');
CREATE INDEX idx_order_code_trgm ON ord."order" USING gin (code gin_trgm_ops);

CREATE TABLE ord.order_media (
  id           uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  order_id     uuid NOT NULL REFERENCES ord."order"(id) ON DELETE CASCADE,
  file_id      uuid NOT NULL,
  type         text NOT NULL CHECK (type IN ('IMAGE','VIDEO','AUDIO','DOC')),
  ai_tags      text[] NOT NULL DEFAULT '{}',
  transcript   text,
  exif_stripped boolean NOT NULL DEFAULT true,
  uploaded_by  uuid NOT NULL,
  visibility   text NOT NULL DEFAULT 'PARTIES' CHECK (visibility IN ('PARTIES','SUPPORT','PUBLIC')),
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE catalog.triage_flow (
  id          uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  category_id uuid NOT NULL REFERENCES catalog.service_category(id),
  version     text NOT NULL,
  nodes       jsonb NOT NULL,          -- درخت پرسش‌ها (قابل ویرایش در پنل، بدون انتشار اپ)
  active      boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ord.order_triage_answer (
  id          uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  order_id    uuid NOT NULL REFERENCES ord."order"(id) ON DELETE CASCADE,
  question_id text NOT NULL,
  answer      jsonb NOT NULL,
  source      text NOT NULL DEFAULT 'USER' CHECK (source IN ('USER','AI','HUMAN_AGENT')),
  confidence  numeric(4,3),
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ord.order_item (
  id         uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  order_id   uuid NOT NULL REFERENCES ord."order"(id) ON DELETE CASCADE,
  kind       text NOT NULL CHECK (kind IN ('SERVICE','MATERIAL','ADDON','TRAVEL','DISCOUNT','PENALTY')),
  title      text NOT NULL,
  qty        numeric(10,3) NOT NULL DEFAULT 1,
  unit_price bigint NOT NULL,
  total      bigint NOT NULL,
  taxable    boolean NOT NULL DEFAULT true,
  receipt_file_id uuid
);
CREATE INDEX idx_order_item_order ON ord.order_item (order_id);

CREATE TABLE ord.order_checklist (
  id            uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  order_id      uuid NOT NULL REFERENCES ord."order"(id) ON DELETE CASCADE,
  phase         text NOT NULL CHECK (phase IN ('BEFORE','DURING','AFTER')),
  item_code     text NOT NULL,
  item_title    text NOT NULL,
  required      boolean NOT NULL DEFAULT true,
  photo_required boolean NOT NULL DEFAULT true,
  done_at       timestamptz,
  done_by       uuid,
  photo_file_id uuid,
  geo           geography(Point,4326),
  note          text,
  UNIQUE (order_id, phase, item_code)
);

CREATE TABLE ord.order_timeline (
  id         uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  order_id   uuid NOT NULL REFERENCES ord."order"(id) ON DELETE CASCADE,
  event_code text NOT NULL,
  actor_id   uuid,
  payload    jsonb NOT NULL DEFAULT '{}'::jsonb,
  at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_timeline_order ON ord.order_timeline (order_id, at);

-- پارتیشن‌شده (نمونه: پارتیشن ماهانه)
CREATE TABLE ord.order_event (
  id             uuid NOT NULL DEFAULT platform.uuid_v7(),
  order_id       uuid NOT NULL,
  event_type     text NOT NULL,
  payload        jsonb NOT NULL DEFAULT '{}'::jsonb,
  correlation_id text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (created_at, id)
) PARTITION BY RANGE (created_at);
CREATE TABLE ord.order_event_default PARTITION OF ord.order_event DEFAULT;

CREATE TABLE ord.order_status_transition (
  id        bigserial PRIMARY KEY,
  order_id  uuid NOT NULL REFERENCES ord."order"(id) ON DELETE CASCADE,
  from_status text, to_status text NOT NULL,
  by_user   uuid, reason text,
  at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ord.booking (
  id           uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  order_id     uuid NOT NULL REFERENCES ord."order"(id) ON DELETE CASCADE,
  provider_id  uuid NOT NULL REFERENCES identity.provider_profile(user_id),
  time_range   tstzrange NOT NULL,
  check_in_at  timestamptz,
  check_out_at timestamptz,
  gps_in       geography(Point,4326),
  gps_out      geography(Point,4326),
  minutes_worked integer,
  EXCLUDE USING gist (provider_id WITH =, time_range WITH &&)   -- جلوگیری از تعارض تقویم
);
CREATE INDEX idx_booking_order ON ord.booking (order_id);

CREATE TABLE ord.job_worklog (
  id            uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  booking_id    uuid NOT NULL REFERENCES ord.booking(id) ON DELETE CASCADE,
  minutes_worked integer NOT NULL,
  notes         text,
  materials_used jsonb NOT NULL DEFAULT '[]'::jsonb,
  approved_by_customer boolean NOT NULL DEFAULT false,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ord.recurring_plan (
  id              uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  customer_id     uuid REFERENCES identity."user"(id),
  org_id          uuid REFERENCES identity.org_account(id),
  service_item_id uuid NOT NULL REFERENCES catalog.service_item(id),
  cadence         text NOT NULL CHECK (cadence IN ('WEEKLY','BIWEEKLY','MONTHLY','QUARTERLY','SEASONAL')),
  next_run_at     timestamptz NOT NULL,
  preferred_slot  jsonb,
  active          boolean NOT NULL DEFAULT true,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CHECK (customer_id IS NOT NULL OR org_id IS NOT NULL)
);

CREATE TABLE ord.maintenance_contract (
  id          uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  org_id      uuid NOT NULL REFERENCES identity.org_account(id) ON DELETE CASCADE,
  title       text NOT NULL,
  sla_terms   jsonb NOT NULL DEFAULT '{}'::jsonb,
  monthly_fee bigint NOT NULL DEFAULT 0,
  included_hours integer NOT NULL DEFAULT 0,
  from_date   date NOT NULL, to_date date,
  status      text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('DRAFT','ACTIVE','EXPIRED','TERMINATED'))
);

-- =====================================================================
-- ۵) DISPATCH — موج‌ها، پیشنهادها، تخصیص، اعزام فوری (۷ جدول)
-- =====================================================================
CREATE TABLE dispatch.dispatch_request (
  id          uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  order_id    uuid NOT NULL REFERENCES ord."order"(id) ON DELETE CASCADE,
  mode        text NOT NULL DEFAULT 'NORMAL' CHECK (mode IN ('NORMAL','EMERGENCY','SCHEDULED','MANUAL')),
  wave_no     smallint NOT NULL DEFAULT 1,
  radius_m    integer NOT NULL DEFAULT 4000,
  status      text NOT NULL DEFAULT 'RUNNING' CHECK (status IN ('RUNNING','ASSIGNED','ESCALATED','FAILED','CANCELLED')),
  policy      jsonb NOT NULL DEFAULT '{}'::jsonb,
  started_at  timestamptz NOT NULL DEFAULT now(),
  expires_at  timestamptz,
  started_by  text NOT NULL DEFAULT 'AUTO' CHECK (started_by IN ('AUTO','OPERATOR'))
);
CREATE INDEX idx_dispatch_order ON dispatch.dispatch_request (order_id, started_at DESC);

CREATE TABLE dispatch.job_offer (
  id               uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  dispatch_id      uuid NOT NULL REFERENCES dispatch.dispatch_request(id) ON DELETE CASCADE,
  order_id         uuid NOT NULL REFERENCES ord."order"(id) ON DELETE CASCADE,
  provider_id      uuid NOT NULL REFERENCES identity.provider_profile(user_id),
  rank_score       numeric(6,4) NOT NULL,
  score_breakdown  jsonb NOT NULL DEFAULT '{}'::jsonb,
  incentive_amount bigint NOT NULL DEFAULT 0,
  offered_at       timestamptz NOT NULL DEFAULT now(),
  expires_at       timestamptz NOT NULL,
  response         text NOT NULL DEFAULT 'PENDING' CHECK (response IN ('PENDING','ACCEPT','REJECT','IGNORE','EXPIRED')),
  responded_at     timestamptz
);
CREATE INDEX idx_offer_provider_open ON dispatch.job_offer (provider_id, expires_at) WHERE response = 'PENDING';
CREATE INDEX idx_offer_order ON dispatch.job_offer (order_id, rank_score DESC);

CREATE TABLE dispatch.assignment (
  id            uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  order_id      uuid NOT NULL REFERENCES ord."order"(id) ON DELETE CASCADE,
  provider_id   uuid NOT NULL REFERENCES identity.provider_profile(user_id),
  assigned_by   text NOT NULL CHECK (assigned_by IN ('AUTO','MANUAL','CUSTOMER_PICK')),
  assigned_by_user uuid,
  reason        text,
  assigned_at   timestamptz NOT NULL DEFAULT now(),
  released_at   timestamptz,
  release_reason text
);
CREATE UNIQUE INDEX uq_active_assignment ON dispatch.assignment (order_id) WHERE released_at IS NULL;

CREATE TABLE dispatch.assignment_rejection (
  id            uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  offer_id      uuid REFERENCES dispatch.job_offer(id) ON DELETE CASCADE,
  provider_id   uuid NOT NULL,
  reason_code   text NOT NULL,
  note          text,
  at            timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE dispatch.dispatch_wave (
  id             uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  dispatch_id    uuid NOT NULL REFERENCES dispatch.dispatch_request(id) ON DELETE CASCADE,
  wave_no        smallint NOT NULL,
  candidate_count integer NOT NULL,
  radius_m       integer NOT NULL,
  wave_deadline  timestamptz NOT NULL,
  outcome        text CHECK (outcome IN ('ACCEPTED','EXPIRED','ESCALATED')),
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (dispatch_id, wave_no)
);

CREATE TABLE dispatch.provider_score_snapshot (
  id          uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  provider_id uuid NOT NULL,
  order_id    uuid,
  context     jsonb NOT NULL DEFAULT '{}'::jsonb,
  components  jsonb NOT NULL,
  total       numeric(6,4) NOT NULL,
  at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_score_provider_time ON dispatch.provider_score_snapshot (provider_id, at DESC);

CREATE TABLE dispatch.emergency_request (
  id                uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  order_id          uuid NOT NULL REFERENCES ord."order"(id) ON DELETE CASCADE,
  category_id       uuid NOT NULL,
  eta_minutes       smallint,
  accepted_provider_id uuid,
  prepay_txn_id     uuid,
  safety_ack_at     timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now()
);

-- =====================================================================
-- ۶) PAY — پرداخت، دفتر کل، Escrow، کیف پول، تسویه، فاکتور (۱۴ جدول)
-- =====================================================================
CREATE TABLE pay.payment_intent (
  id              uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  order_id        uuid REFERENCES ord."order"(id),
  payer_id        uuid NOT NULL,
  org_id          uuid,
  amount          bigint NOT NULL CHECK (amount > 0),
  currency        char(3) NOT NULL DEFAULT 'IRR',
  method          text NOT NULL CHECK (method IN ('IPG','WALLET','BNPL','CASH','CREDIT_LINE')),
  purpose         text NOT NULL DEFAULT 'ORDER_PAYMENT' CHECK (purpose IN
                  ('ORDER_PAYMENT','MILESTONE','TOPUP','SUBSCRIPTION','PENALTY')),
  status          text NOT NULL DEFAULT 'INIT' CHECK (status IN
                  ('INIT','PENDING','AUTHORIZED','SUCCEEDED','FAILED','CANCELLED','EXPIRED')),
  idempotency_key text UNIQUE NOT NULL,
  psp_ref         text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_intent_order ON pay.payment_intent (order_id);

CREATE TABLE pay.payment_txn (
  id             uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  intent_id      uuid NOT NULL REFERENCES pay.payment_intent(id) ON DELETE CASCADE,
  psp            text NOT NULL,
  psp_txn_id     text,
  amount         bigint NOT NULL,
  fee            bigint NOT NULL DEFAULT 0,
  status         text NOT NULL CHECK (status IN ('INIT','PENDING','SUCCESS','FAILED','REVERSED')),
  error_code     text,
  raw_response   jsonb NOT NULL DEFAULT '{}'::jsonb,
  at             timestamptz NOT NULL DEFAULT now(),
  UNIQUE (psp, psp_txn_id)
);

CREATE TABLE pay.ledger_account (
  id            uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  code          text UNIQUE NOT NULL,             -- 1100-CUSTOMER-FUNDS ...
  name          text NOT NULL,
  kind          text NOT NULL CHECK (kind IN ('ASSET','LIABILITY','REVENUE','EXPENSE','EQUITY')),
  owner_type    text CHECK (owner_type IN ('PLATFORM','USER','PROVIDER','ORG','PSP','TAX','INSURER')),
  owner_id      uuid,
  normal_balance text NOT NULL CHECK (normal_balance IN ('DEBIT','CREDIT')),
  currency      char(3) NOT NULL DEFAULT 'IRR',
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE pay.ledger_entry (
  id          uuid NOT NULL DEFAULT platform.uuid_v7(),
  txn_id      uuid NOT NULL,                      -- گروه قید یک تراکنش
  account_id  uuid NOT NULL REFERENCES pay.ledger_account(id),
  direction   text NOT NULL CHECK (direction IN ('DEBIT','CREDIT')),
  amount      bigint NOT NULL CHECK (amount > 0),
  currency    char(3) NOT NULL DEFAULT 'IRR',
  ref_type    text NOT NULL,                      -- order | claim | payout | adjustment
  ref_id      uuid,
  memo        text,
  hash_prev   text,
  hash_self   text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (created_at, id)
) PARTITION BY RANGE (created_at);
CREATE TABLE pay.ledger_entry_default PARTITION OF pay.ledger_entry DEFAULT;
CREATE INDEX idx_ledger_txn ON pay.ledger_entry (txn_id);
CREATE INDEX idx_ledger_account_time ON pay.ledger_entry (account_id, created_at DESC);

CREATE TABLE pay.escrow_hold (
  id              uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  order_id        uuid NOT NULL REFERENCES ord."order"(id),
  amount          bigint NOT NULL,
  released_amount bigint NOT NULL DEFAULT 0,
  refunded_amount bigint NOT NULL DEFAULT 0,
  status          text NOT NULL DEFAULT 'HELD' CHECK (status IN ('HELD','PARTIAL','RELEASED','REFUNDED','FROZEN','EXPIRED')),
  held_at         timestamptz NOT NULL DEFAULT now(),
  expires_at      timestamptz,
  CHECK (released_amount + refunded_amount <= amount)
);
CREATE UNIQUE INDEX uq_escrow_open ON pay.escrow_hold (order_id) WHERE status IN ('HELD','PARTIAL','FROZEN');

CREATE TABLE pay.milestone (
  id             uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  order_id       uuid NOT NULL REFERENCES ord."order"(id) ON DELETE CASCADE,
  seq            smallint NOT NULL,
  title          text NOT NULL,
  share_percent  numeric(5,2) NOT NULL CHECK (share_percent > 0 AND share_percent <= 100),
  condition      jsonb NOT NULL DEFAULT '{}'::jsonb,
  status         text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','SUBMITTED','APPROVED','REJECTED','PAID')),
  proof_media_ids uuid[] NOT NULL DEFAULT '{}',
  submitted_at   timestamptz,
  approved_at    timestamptz,
  approved_by    uuid,
  UNIQUE (order_id, seq)
);

CREATE TABLE pay.escrow_release (
  id           uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  hold_id      uuid NOT NULL REFERENCES pay.escrow_hold(id) ON DELETE CASCADE,
  milestone_id uuid REFERENCES pay.milestone(id),
  amount       bigint NOT NULL,
  type         text NOT NULL CHECK (type IN ('FULL','PARTIAL','RELEASE_TO_PROVIDER','REFUND','PENALTY')),
  triggered_by text NOT NULL CHECK (triggered_by IN ('CUSTOMER_APPROVAL','WINDOW_EXPIRED','SUPPORT_DECISION','AUTO_RULE','COURT_ORDER')),
  approved_by  uuid,
  at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE pay.wallet (
  id             uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  owner_type     text NOT NULL CHECK (owner_type IN ('USER','PROVIDER','ORG')),
  owner_id       uuid NOT NULL,
  balance_cached bigint NOT NULL DEFAULT 0,     -- Cache از دفتر کل؛ منبع حقیقت: ledger_entry
  updated_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (owner_type, owner_id)
);

CREATE TABLE pay.wallet_transaction (
  id        uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  wallet_id uuid NOT NULL REFERENCES pay.wallet(id) ON DELETE CASCADE,
  kind      text NOT NULL CHECK (kind IN ('TOPUP','PAYMENT','REFUND','PAYOUT','BONUS','PENALTY','FEE','ESCROW_HOLD','ESCROW_RELEASE')),
  amount    bigint NOT NULL,                     -- علامت‌دار
  ref_type  text, ref_id uuid,
  balance_after bigint,
  at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_wtx_wallet ON pay.wallet_transaction (wallet_id, at DESC);

CREATE TABLE pay.payout_batch (
  id          uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  cycle_date  date NOT NULL,
  status      text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT','APPROVED','SENT','SETTLED','FAILED')),
  total_amount bigint NOT NULL DEFAULT 0,
  item_count  integer NOT NULL DEFAULT 0,
  approved_by uuid, sent_at timestamptz,
  UNIQUE (cycle_date)
);

CREATE TABLE pay.payout_item (
  id          uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  batch_id    uuid NOT NULL REFERENCES pay.payout_batch(id) ON DELETE CASCADE,
  provider_id uuid NOT NULL,
  iban_hash   text NOT NULL,
  gross_amount bigint NOT NULL,
  commission  bigint NOT NULL,
  tax         bigint NOT NULL DEFAULT 0,
  net_amount  bigint NOT NULL,
  status      text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','SENT','SETTLED','FAILED','REVERSED')),
  bank_ref    text,
  error_note  text
);

CREATE TABLE pay.invoice (
  id            uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  number        text UNIQUE NOT NULL,
  order_id      uuid REFERENCES ord."order"(id),
  org_id        uuid REFERENCES identity.org_account(id),
  buyer_snapshot jsonb NOT NULL,
  subtotal      bigint NOT NULL,
  discount      bigint NOT NULL DEFAULT 0,
  vat           bigint NOT NULL DEFAULT 0,
  total         bigint NOT NULL,
  currency      char(3) NOT NULL DEFAULT 'IRR',
  status        text NOT NULL DEFAULT 'ISSUED' CHECK (status IN ('DRAFT','ISSUED','PAID','VOID','CREDIT_NOTE')),
  pdf_file_id   uuid,
  issued_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE pay.tax_invoice (
  id             uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  invoice_id     uuid NOT NULL REFERENCES pay.invoice(id) ON DELETE CASCADE,
  tax_id_unique  text,
  submit_status  text NOT NULL DEFAULT 'PENDING' CHECK (submit_status IN ('PENDING','SENT','ACCEPTED','REJECTED','CORRECTED')),
  response_code  text, response_message text,
  xml_file_id    uuid,
  submitted_at   timestamptz,
  retry_count    smallint NOT NULL DEFAULT 0
);

CREATE TABLE pay.refund (
  id               uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  payment_intent_id uuid NOT NULL REFERENCES pay.payment_intent(id),
  order_id         uuid REFERENCES ord."order"(id),
  amount           bigint NOT NULL,
  reason_code      text NOT NULL,
  destination      text NOT NULL CHECK (destination IN ('WALLET','IPG')),
  status           text NOT NULL DEFAULT 'REQUESTED' CHECK (status IN ('REQUESTED','APPROVED','REJECTED','PROCESSED','FAILED')),
  requested_by     uuid, approved_by uuid,
  at               timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE pay.bnpl_contract (
  id               uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  order_id         uuid NOT NULL REFERENCES ord."order"(id),
  partner          text NOT NULL,
  approved_amount  bigint NOT NULL,
  down_payment     bigint NOT NULL DEFAULT 0,
  installment_count smallint NOT NULL,
  schedule         jsonb NOT NULL,
  external_ref     text,
  status           text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('APPLIED','ACTIVE','SETTLED','DEFAULTED','CANCELLED'))
);

CREATE TABLE pay.reconciliation_run (
  id          uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  run_date    date NOT NULL,
  source      text NOT NULL CHECK (source IN ('PSP','BANK','LEDGER','TAX')),
  matched     integer NOT NULL DEFAULT 0,
  diff_count  integer NOT NULL DEFAULT 0,
  status      text NOT NULL DEFAULT 'RUNNING' CHECK (status IN ('RUNNING','OK','MISMATCH','FAILED')),
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (run_date, source)
);

CREATE TABLE pay.reconciliation_item (
  id           uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  run_id       uuid NOT NULL REFERENCES pay.reconciliation_run(id) ON DELETE CASCADE,
  ref          text NOT NULL,
  expected     bigint, actual bigint,
  status       text NOT NULL CHECK (status IN ('MATCH','MISSING_IN_LEDGER','MISSING_IN_PSP','AMOUNT_DIFF')),
  note         text
);

-- =====================================================================
-- ۷) TRUST — نظر، کیفیت، گارانتی، بیمه، خسارت، اختلاف (۱۰ جدول)
-- =====================================================================
CREATE TABLE trust.review (
  id           uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  order_id     uuid UNIQUE NOT NULL REFERENCES ord."order"(id),
  customer_id  uuid NOT NULL, provider_id uuid NOT NULL,
  text         text,
  is_verified  boolean NOT NULL DEFAULT true,
  sentiment    numeric(4,3),
  topics       text[] NOT NULL DEFAULT '{}',
  moderation_status text NOT NULL DEFAULT 'PUBLISHED' CHECK (moderation_status IN
               ('PENDING','PUBLISHED','FLAGGED','HIDDEN','REMOVED')),
  published_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_review_provider ON trust.review (provider_id, published_at DESC);

CREATE TABLE trust.review_dimension (
  review_id uuid REFERENCES trust.review(id) ON DELETE CASCADE,
  dimension text NOT NULL CHECK (dimension IN ('PUNCTUALITY','QUALITY','TIDINESS','PRICE_ACCURACY','CONDUCT')),
  score     smallint NOT NULL CHECK (score BETWEEN 1 AND 5),
  PRIMARY KEY (review_id, dimension)
);

CREATE TABLE trust.review_appeal (
  id          uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  review_id   uuid NOT NULL REFERENCES trust.review(id) ON DELETE CASCADE,
  filed_by    uuid NOT NULL,
  reason      text NOT NULL,
  evidence    jsonb NOT NULL DEFAULT '[]'::jsonb,
  decision    text CHECK (decision IN ('UPHELD','MODIFIED','REMOVED')),
  decided_by  uuid, decided_at timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE trust.quality_check (
  id           uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  order_id     uuid NOT NULL REFERENCES ord."order"(id) ON DELETE CASCADE,
  type         text NOT NULL CHECK (type IN ('AUTO','HUMAN')),
  score        numeric(5,2),
  checklist    jsonb NOT NULL DEFAULT '{}'::jsonb,
  result       text CHECK (result IN ('PASS','FAIL','NEEDS_REWORK')),
  checked_by   uuid, at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE trust.rework_case (
  id            uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  order_id      uuid NOT NULL REFERENCES ord."order"(id) ON DELETE CASCADE,
  reported_by   uuid NOT NULL,
  symptom       text NOT NULL,
  root_cause    text,
  fix_provider_id uuid,
  fix_cost      bigint DEFAULT 0,
  charged_to_provider boolean NOT NULL DEFAULT false,
  status        text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','SCHEDULED','FIXED','CLOSED','REJECTED')),
  closed_at     timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE trust.warranty (
  id            uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  order_id      uuid NOT NULL REFERENCES ord."order"(id) ON DELETE CASCADE,
  category_id   uuid NOT NULL,
  terms_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  started_at    timestamptz NOT NULL DEFAULT now(),
  expires_at    timestamptz NOT NULL,
  status        text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','CLAIMED','EXPIRED','VOID'))
);
CREATE INDEX idx_warranty_active ON trust.warranty (expires_at) WHERE status = 'ACTIVE';

CREATE TABLE trust.insurance_policy (
  id            uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  partner_name  text NOT NULL,
  master_policy_no text NOT NULL,
  premium_rate  numeric(5,4) NOT NULL DEFAULT 0.0060,
  valid_from    date NOT NULL, valid_to date NOT NULL,
  status        text NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE trust.insurance_coverage (
  id            uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  policy_id     uuid NOT NULL REFERENCES trust.insurance_policy(id) ON DELETE CASCADE,
  category_id   uuid,
  max_amount    bigint NOT NULL,
  deductible    bigint NOT NULL DEFAULT 0,
  notes         text
);

CREATE TABLE trust.claim (
  id               uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  order_id         uuid NOT NULL REFERENCES ord."order"(id),
  claimant_id      uuid NOT NULL,
  type             text NOT NULL CHECK (type IN ('ACCIDENT','DAMAGE','INJURY','THEFT','OTHER')),
  description      text NOT NULL,
  amount_requested bigint NOT NULL,
  evidence         jsonb NOT NULL DEFAULT '[]'::jsonb,
  risk_score       numeric(5,2),
  status           text NOT NULL DEFAULT 'SUBMITTED' CHECK (status IN
                   ('SUBMITTED','IN_REVIEW','AWAITING_EVIDENCE','APPROVED','PARTIAL','REJECTED','PAID','WITHDRAWN')),
  sla_due_at       timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_claim_order ON trust.claim (order_id);

CREATE TABLE trust.claim_assessment (
  id             uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  claim_id       uuid NOT NULL REFERENCES trust.claim(id) ON DELETE CASCADE,
  method         text NOT NULL CHECK (method IN ('AUTO','EXPERT','PARTNER')),
  decision       text NOT NULL CHECK (decision IN ('APPROVE','PARTIAL','REJECT','ESCALATE')),
  amount_approved bigint NOT NULL DEFAULT 0,
  assessor_id    uuid,
  notes          text,
  features       jsonb NOT NULL DEFAULT '{}'::jsonb,
  at             timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE trust.dispute (
  id            uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  order_id      uuid NOT NULL REFERENCES ord."order"(id),
  opened_by     uuid NOT NULL,
  category      text NOT NULL CHECK (category IN ('QUALITY','PRICE','BEHAVIOR','DAMAGE','NO_SHOW','OTHER')),
  description   text NOT NULL,
  freeze_escrow boolean NOT NULL DEFAULT true,
  status        text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','NEGOTIATION','EXPERT_REVIEW','RESOLVED','ESCALATED','REJECTED')),
  sla_due_at    timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  resolved_at   timestamptz
);

CREATE TABLE trust.dispute_message (
  id          uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  dispute_id  uuid NOT NULL REFERENCES trust.dispute(id) ON DELETE CASCADE,
  sender_id   uuid NOT NULL,
  sender_role text NOT NULL CHECK (sender_role IN ('CUSTOMER','PROVIDER','SUPPORT','EXPERT','SYSTEM')),
  body        text,
  attachments jsonb NOT NULL DEFAULT '[]'::jsonb,
  at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE trust.dispute_resolution (
  id           uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  dispute_id   uuid UNIQUE NOT NULL REFERENCES trust.dispute(id) ON DELETE CASCADE,
  decision     text NOT NULL CHECK (decision IN ('REWORK_FREE','PARTIAL_REFUND','FULL_REFUND','RELEASE_FULL','PENALTY','COMPROMISE')),
  amounts      jsonb NOT NULL DEFAULT '{}'::jsonb,
  rationale    text NOT NULL,
  decided_by   uuid NOT NULL,
  at           timestamptz NOT NULL DEFAULT now()
);

-- =====================================================================
-- ۸) RISK — سیگنال، امتیاز، پرونده، گراف، تبانی، لیست سیاه (۶ جدول)
-- =====================================================================
CREATE TABLE risk.risk_signal (
  id          uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  entity_type text NOT NULL CHECK (entity_type IN ('USER','PROVIDER','ORDER','DEVICE','PAYMENT','REVIEW')),
  entity_id   uuid NOT NULL,
  signal_code text NOT NULL,
  value       numeric, value_text text,
  source      text NOT NULL,
  at          timestamptz NOT NULL DEFAULT now(),
  ttl_seconds integer
);
CREATE INDEX idx_signal_entity ON risk.risk_signal (entity_type, entity_id, at DESC);

CREATE TABLE risk.risk_score (
  id          uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  entity_type text NOT NULL, entity_id uuid NOT NULL,
  scope       text NOT NULL CHECK (scope IN ('ONBOARDING','ORDER','PAYOUT','REVIEW','CLAIM','SESSION')),
  score       numeric(5,2) NOT NULL,
  band        text NOT NULL CHECK (band IN ('LOW','MEDIUM','HIGH')),
  model_version text,
  features    jsonb NOT NULL DEFAULT '{}'::jsonb,
  computed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_riskscore_entity ON risk.risk_score (entity_type, entity_id, computed_at DESC);

CREATE TABLE risk.risk_case (
  id          uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  entity_type text NOT NULL, entity_id uuid NOT NULL,
  order_id    uuid,
  band        text NOT NULL,
  pattern     text,
  status      text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','IN_REVIEW','RESOLVED','BLOCKED','FALSE_POSITIVE')),
  assigned_to uuid,
  decision    text, decided_at timestamptz, notes text,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_riskcase_open ON risk.risk_case (status, band);

CREATE TABLE risk.entity_link (
  id          bigserial PRIMARY KEY,
  left_type   text NOT NULL, left_id uuid NOT NULL,
  right_type  text NOT NULL, right_id uuid NOT NULL,
  link_type   text NOT NULL CHECK (link_type IN ('DEVICE','IBAN','ADDRESS','IP','PHONE','EMAIL','COUPON')),
  weight      numeric(4,3) NOT NULL DEFAULT 1.0,
  first_seen  timestamptz NOT NULL DEFAULT now(),
  last_seen   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (left_type, left_id, right_type, right_id, link_type)
);

CREATE TABLE risk.collusion_alert (
  id           uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  pair_hash    text NOT NULL,
  provider_id  uuid NOT NULL, customer_id uuid NOT NULL,
  pattern      text NOT NULL CHECK (pattern IN ('OFF_PLATFORM','CANCELLATION_RING','REVIEW_RING','FAKE_CLAIM','GPS_SPOOF')),
  evidence     jsonb NOT NULL DEFAULT '{}'::jsonb,
  confidence   numeric(4,3) NOT NULL,
  estimated_loss bigint,
  status       text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','CONFIRMED','DISMISSED','ACTIONED')),
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (pair_hash, pattern, created_at)
);

CREATE TABLE risk.blacklist_entry (
  id         uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  type       text NOT NULL CHECK (type IN ('PHONE','DEVICE','NATIONAL_ID','IBAN','IP','EMAIL')),
  value_hash text NOT NULL,
  reason     text NOT NULL,
  added_by   uuid NOT NULL,
  expires_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (type, value_hash)
);

-- =====================================================================
-- ۹) COMMS & CRM — چت، تماس، ویدیو، تیکت، کمپین، دانش، اعلان (۹ جدول)
-- =====================================================================
CREATE TABLE comms.conversation (
  id          uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  order_id    uuid REFERENCES ord."order"(id),
  type        text NOT NULL CHECK (type IN ('CUSTOMER_PROVIDER','SUPPORT','BOT','EXPERT_PANEL')),
  participants uuid[] NOT NULL DEFAULT '{}',
  status      text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','CLOSED','ARCHIVED')),
  last_message_at timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_conv_order ON comms.conversation (order_id);

CREATE TABLE comms.message (
  id              uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  conversation_id uuid NOT NULL REFERENCES comms.conversation(id) ON DELETE CASCADE,
  sender_id       uuid,
  sender_role     text NOT NULL CHECK (sender_role IN ('CUSTOMER','PROVIDER','SUPPORT','BOT','SYSTEM')),
  type            text NOT NULL DEFAULT 'TEXT' CHECK (type IN ('TEXT','IMAGE','VIDEO','AUDIO','FILE','LOCATION','SYSTEM','QUICK_REPLY')),
  body            text,
  redacted        boolean NOT NULL DEFAULT false,
  redacted_reason text,
  delivered_at    timestamptz, read_at timestamptz,
  at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_msg_conv ON comms.message (conversation_id, at);

CREATE TABLE comms.message_attachment (
  id         uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  message_id uuid NOT NULL REFERENCES comms.message(id) ON DELETE CASCADE,
  file_id    uuid NOT NULL, kind text NOT NULL, size_bytes bigint, checksum text
);

CREATE TABLE comms.masked_call (
  id            uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  order_id      uuid REFERENCES ord."order"(id),
  caller_id     uuid NOT NULL, callee_id uuid NOT NULL,
  virtual_number text NOT NULL,
  direction     text NOT NULL CHECK (direction IN ('OUTBOUND','INBOUND')),
  started_at    timestamptz NOT NULL DEFAULT now(),
  ended_at      timestamptz,
  duration_seconds integer,
  recording_file_id uuid,
  consent_announced boolean NOT NULL DEFAULT true,
  status        text NOT NULL DEFAULT 'ANSWERED' CHECK (status IN ('RINGING','ANSWERED','MISSED','FAILED','BLOCKED'))
);

CREATE TABLE comms.call_transcript (
  id         uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  call_id    uuid NOT NULL REFERENCES comms.masked_call(id) ON DELETE CASCADE,
  text       text NOT NULL,
  segments   jsonb NOT NULL DEFAULT '[]'::jsonb,
  keywords   text[] NOT NULL DEFAULT '{}',
  pii_flags  text[] NOT NULL DEFAULT '{}',
  redacted_text text,
  model_version text,
  at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE comms.video_session (
  id            uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  order_id      uuid NOT NULL REFERENCES ord."order"(id),
  stage         text NOT NULL CHECK (stage IN ('REMOTE_INSPECTION','EXPERT_PANEL','DISPUTE_REVIEW')),
  participants  uuid[] NOT NULL,
  recording_consent boolean NOT NULL DEFAULT false,
  recording_file_id uuid,
  duration_seconds integer,
  notes         text,
  started_at    timestamptz NOT NULL DEFAULT now(),
  ended_at      timestamptz
);

CREATE TABLE comms.support_ticket (
  id            uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  code          text UNIQUE NOT NULL,
  channel       text NOT NULL CHECK (channel IN ('APP','WEB','CALL','EMAIL','SOCIAL','IN_ORDER')),
  category      text NOT NULL,
  priority      smallint NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 4),
  level         smallint NOT NULL DEFAULT 1 CHECK (level BETWEEN 1 AND 3),
  subject       text NOT NULL,
  requester_id  uuid NOT NULL,
  related_order_id uuid,
  assignee_id   uuid,
  sla_due_at    timestamptz,
  status        text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','PENDING_USER','PENDING_INTERNAL','ESCALATED','RESOLVED','CLOSED')),
  created_at    timestamptz NOT NULL DEFAULT now(),
  resolved_at   timestamptz
);
CREATE INDEX idx_ticket_open ON comms.support_ticket (status, sla_due_at);

CREATE TABLE comms.ticket_event (
  id        uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  ticket_id uuid NOT NULL REFERENCES comms.support_ticket(id) ON DELETE CASCADE,
  type      text NOT NULL,
  from_value text, to_value text,
  note      text, actor_id uuid,
  at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE comms.knowledge_article (
  id           uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  slug         text UNIQUE NOT NULL,
  title        text NOT NULL,
  body         text NOT NULL,
  tags         text[] NOT NULL DEFAULT '{}',
  audience     text NOT NULL DEFAULT 'PUBLIC' CHECK (audience IN ('PUBLIC','SUPPORT','BOT')),
  version      smallint NOT NULL DEFAULT 1,
  published_at timestamptz
);
CREATE INDEX idx_kb_fts ON comms.knowledge_article USING gin (to_tsvector('simple', title || ' ' || body));

CREATE TABLE comms.notification_template (
  id       uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  code     text NOT NULL,
  channel  text NOT NULL CHECK (channel IN ('PUSH','SMS','EMAIL','IN_APP','WHATSAPP')),
  locale   text NOT NULL DEFAULT 'fa-IR',
  title    text, body text NOT NULL,
  variables text[] NOT NULL DEFAULT '{}',
  priority text NOT NULL DEFAULT 'NORMAL' CHECK (priority IN ('HIGH','NORMAL','LOW')),
  active   boolean NOT NULL DEFAULT true,
  UNIQUE (code, channel, locale)
);

CREATE TABLE comms.notification_log (
  id         uuid NOT NULL DEFAULT platform.uuid_v7(),
  user_id    uuid NOT NULL,
  channel    text NOT NULL,
  template_code text NOT NULL,
  payload    jsonb NOT NULL DEFAULT '{}'::jsonb,
  status     text NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED','SENT','DELIVERED','FAILED','SUPPRESSED')),
  fail_reason text,
  sent_at    timestamptz, delivered_at timestamptz, opened_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (created_at, id)
) PARTITION BY RANGE (created_at);
CREATE TABLE comms.notification_log_default PARTITION OF comms.notification_log DEFAULT;

CREATE TABLE comms.notification_preference (
  user_id uuid REFERENCES identity."user"(id) ON DELETE CASCADE,
  channel text NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  quiet_hours tstzrange,
  PRIMARY KEY (user_id, channel)
);

CREATE TABLE comms.crm_segment (
  id     uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  name   text NOT NULL,
  rules  jsonb NOT NULL,
  size_cached integer,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE comms.campaign (
  id         uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  segment_id uuid REFERENCES comms.crm_segment(id),
  channel    text NOT NULL,
  template_code text NOT NULL,
  budget     bigint DEFAULT 0,
  scheduled_at timestamptz,
  status     text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT','SCHEDULED','RUNNING','DONE','CANCELLED')),
  created_by uuid
);

-- =====================================================================
-- ۱۰) AI / PLATFORM / ANALYTICS — استنتاج، برچسب، فلگ، حسابرسی
-- =====================================================================
CREATE TABLE ai.ai_inference (
  id            uuid NOT NULL DEFAULT platform.uuid_v7(),
  model_name    text NOT NULL,
  model_version text NOT NULL,
  purpose       text NOT NULL CHECK (purpose IN
                ('CATEGORY','SEVERITY','PRICE_ESTIMATE','ETA','FRAUD','TRANSCRIBE','FACE_MATCH','QUALITY_PHOTO','CHATBOT')),
  input_ref     text NOT NULL,
  output        jsonb NOT NULL,
  confidence    numeric(4,3),
  latency_ms    integer,
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (created_at, id)
) PARTITION BY RANGE (created_at);
CREATE TABLE ai.ai_inference_default PARTITION OF ai.ai_inference DEFAULT;
CREATE INDEX idx_ai_ref ON ai.ai_inference (input_ref);

CREATE TABLE ai.ai_label (
  id            uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  input_ref     text NOT NULL,
  inference_id  uuid,
  label         jsonb NOT NULL,
  labeler_type  text NOT NULL CHECK (labeler_type IN ('HUMAN','AUTO','MODEL')),
  labeler_id    uuid,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE platform.provider_location_ping (
  provider_id uuid NOT NULL,
  location    geography(Point,4326) NOT NULL,
  speed_kmh   numeric(6,2),
  accuracy_m  numeric(6,2),
  is_mock     boolean NOT NULL DEFAULT false,
  order_id    uuid,
  created_at  timestamptz NOT NULL DEFAULT now()
) PARTITION BY RANGE (created_at);
CREATE TABLE platform.provider_location_ping_default PARTITION OF platform.provider_location_ping DEFAULT;
CREATE INDEX idx_ping_provider ON platform.provider_location_ping (provider_id, created_at DESC);

CREATE TABLE platform.feature_flag (
  key             text PRIMARY KEY,
  description     text,
  rules           jsonb NOT NULL DEFAULT '{}'::jsonb,
  rollout_percent smallint NOT NULL DEFAULT 0 CHECK (rollout_percent BETWEEN 0 AND 100),
  kill_switch     boolean NOT NULL DEFAULT false,
  updated_by      uuid,
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE platform.experiment_assignment (
  id        uuid PRIMARY KEY DEFAULT platform.uuid_v7(),
  flag_key  text NOT NULL REFERENCES platform.feature_flag(key),
  user_id   uuid NOT NULL,
  variant   text NOT NULL,
  assigned_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (flag_key, user_id)
);

CREATE TABLE platform.audit_log (
  id             uuid NOT NULL DEFAULT platform.uuid_v7(),
  actor_id       uuid,
  actor_role     text,
  action         text NOT NULL,
  resource_type  text NOT NULL,
  resource_id    uuid,
  before_data    jsonb,
  after_data     jsonb,
  reason         text,
  ip             inet,
  correlation_id text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (created_at, id)
) PARTITION BY RANGE (created_at);
CREATE TABLE platform.audit_log_default PARTITION OF platform.audit_log DEFAULT;
CREATE INDEX idx_audit_resource ON platform.audit_log (resource_type, resource_id, created_at DESC);
CREATE INDEX idx_audit_actor ON platform.audit_log (actor_id, created_at DESC);

CREATE TABLE platform.idempotency_key (
  key         text PRIMARY KEY,
  scope       text NOT NULL,
  request_hash text NOT NULL,
  response    jsonb,
  created_at  timestamptz NOT NULL DEFAULT now(),
  expires_at  timestamptz NOT NULL
);

CREATE TABLE analytics.analytics_daily_agg (
  date          date NOT NULL,
  zone_id       uuid NOT NULL,
  category_id   uuid NOT NULL,
  orders_count  integer NOT NULL DEFAULT 0,
  gmv           bigint NOT NULL DEFAULT 0,
  take_rate     numeric(5,4),
  fulfillment_rate numeric(5,4),
  tta_seconds_avg integer,
  csat          numeric(3,2),
  cancellations integer NOT NULL DEFAULT 0,
  PRIMARY KEY (date, zone_id, category_id)
);

-- =====================================================================
-- VIEWS — نمایش‌های کلیدی
-- =====================================================================
CREATE OR REPLACE VIEW pay.v_wallet_balance AS
SELECT a.owner_type, a.owner_id, a.id AS account_id,
       COALESCE(SUM(CASE WHEN e.direction = a.normal_balance THEN e.amount ELSE -e.amount END), 0) AS balance
FROM pay.ledger_account a
LEFT JOIN pay.ledger_entry e ON e.account_id = a.id
WHERE a.owner_id IS NOT NULL
GROUP BY a.owner_type, a.owner_id, a.id, a.normal_balance;

CREATE OR REPLACE VIEW trust.v_provider_display_rating AS
SELECT p.user_id AS provider_id,
       ROUND(
         0.5 * COALESCE((SELECT AVG(d.score) FROM trust.review r
                         JOIN trust.review_dimension d ON d.review_id = r.id
                         WHERE r.provider_id = p.user_id AND r.published_at > now() - interval '90 days'), p.rating_avg)
       + 0.3 * (CASE WHEN p.rating_count = 0 THEN 3.9
                     ELSE (p.rating_count::numeric/(p.rating_count+10)) * p.rating_avg
                          + (10::numeric/(p.rating_count+10)) * 3.9 END)
       + 0.2 * LEAST(1.0, p.on_time_rate) * 5
       , 2) AS display_rating
FROM identity.provider_profile p;

-- =====================================================================
-- SEED — حداقل داده‌های پایه (قابل انتقال به پنل مدیریت)
-- =====================================================================
INSERT INTO pay.ledger_account (code, name, kind, owner_type, normal_balance) VALUES
 ('1100-CUSTOMER-FUNDS','وجوه دریافتی از مشتریان','ASSET','PLATFORM','DEBIT'),
 ('2100-BANK-CLEARING','حساب تسویه بانکی','ASSET','PLATFORM','DEBIT'),
 ('2200-ESCROW-LIABILITY','بدهی نگهداشت (Escrow)','LIABILITY','PLATFORM','CREDIT'),
 ('2300-PROVIDER-WALLET','کیف پول متخصصان','LIABILITY','PROVIDER','CREDIT'),
 ('2400-PAYABLE-TAX','مالیات payable','LIABILITY','TAX','CREDIT'),
 ('2500-PAYABLE-INSURER','سهم بیمه‌گذار','LIABILITY','INSURER','CREDIT'),
 ('4100-COMMISSION-REVENUE','درآمد کمیسیون پلتفرم','REVENUE','PLATFORM','CREDIT'),
 ('4200-SUBSCRIPTION-REVENUE','درآمد اشتراک','REVENUE','PLATFORM','CREDIT'),
 ('5100-PAYMENT-FEE','کارمزد درگاه','EXPENSE','PLATFORM','DEBIT'),
 ('5200-COMPENSATION','جبران خسارت و تخفیف جبرانی','EXPENSE','PLATFORM','DEBIT')
ON CONFLICT (code) DO NOTHING;

INSERT INTO identity.role (code, title_fa) VALUES
 ('CUSTOMER','مشتری خانگی'), ('ORG_MEMBER','کاربر سازمانی'),
 ('PROVIDER','متخصص'), ('PROVIDER_ORG_ADMIN','مدیر شرکت خدماتی'),
 ('OPERATOR','اپراتور دیسپچ'), ('SUPPORT_L1','پشتیبانی سطح ۱'),
 ('SUPPORT_L2','پشتیبانی فنی سطح ۲'), ('QUALITY_EXPERT','کارشناس کیفیت/بیمه'),
 ('FINANCE','مالی'), ('RISK_ANALYST','کارشناس ریسک'),
 ('CONTENT_MANAGER','مدیر محتوا'), ('ADMIN','مدیر پلتفرم')
ON CONFLICT (code) DO NOTHING;

INSERT INTO platform.feature_flag (key, description, rollout_percent) VALUES
 ('dynamic_pricing','موتور قیمت‌گذاری پویا',100),
 ('surge_pricing','افزایش قیمت پیک (سقف ۱.۵)',100),
 ('emergency_dispatch','اعزام فوری',50),
 ('ai_intake','تحلیل هوشمند ورودی',30),
 ('smart_dispatch_v2','موتور تطبیق نسخه ۲',20),
 ('chatbot_support','چت‌بات پیش‌پشتیبان',40)
ON CONFLICT (key) DO NOTHING;

-- تریگرهای updated_at (برای جداول اصلی)
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['identity."user"','identity.provider_profile','ord."order"',
                           'pay.payment_intent','pay.wallet']
  LOOP
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE UPDATE ON %s FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at();',
      'trg_touch_' || replace(replace(t, '"', ''), '.', '_'),
      t);
  END LOOP;
END $$;

-- =====================================================================
-- نکات عملیاتی
--  ۱) پارتیشن‌ها را با cron/Job ماهانه بسازید:
--     CREATE TABLE ord.order_event_1405_08 PARTITION OF ord.order_event
--       FOR VALUES FROM ('1405-08-01') TO ('1405-09-01');  -- با تاریخ میلادی معادل
--  ۲) نگهداری: مالی ۷ سال، فنی ۱۸ ماه، رسانه سفارش ۱۸ ماه، لاگ دسترسی ۷ سال
--  ۳) بکاپ: full روزانه + WAL archiving؛ تمرین بازیابی فصلی (RPO ≤ ۵ دقیقه، RTO ≤ ۳۰ دقیقه)
--  ۴) دسترسی اپلیکیشن با کاربر جداگانه و RLS برای داده سازمانی (org_id)
-- =====================================================================
