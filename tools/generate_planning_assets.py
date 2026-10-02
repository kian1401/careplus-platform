#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
تولید دو خروجی برنامه‌ریزی:
  1) db/data_dictionary.csv  — واژه‌نامه داده (۱۱۳ جدول)
  2) rfp/estimate.xlsx       — برآورد کار، سناریوهای هزینه، هزینه جاری، تیم، مراحل پرداخت
اجرا:  python3 tools/generate_planning_assets.py
"""
import csv, os
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------- داده واژه‌نامه
# (schema, table, domain_fa, key_columns, desc_fa, retention)
ROWS = [
("identity","user","هویت","id, phone_hash, national_id_hash, status","هویت واحد هر شخص؛ شماره به‌صورت رمزنگاری و ایندکس‌شده با هش","۷ سال پس از حذف حساب"),
("identity","user_profile","هویت","user_id, full_name, addresses, preferences","پروفایل نمایشی و ترجیحات کاربر","تا حذف حساب"),
("identity","auth_credential","هویت","user_id, type, secret_hash, public_key","هر روش ورود یک ردیف (OTP/OAuth/Passkey)","تا حذف حساب"),
("identity","auth_session","هویت","refresh_token_hash, device_id, risk_score","نشست‌ها با چرخش توکن و تشخیص استفاده مجدد","۱۸ ماه"),
("identity","otp_challenge","هویت","phone_hash, code_hash, attempts, expires_at","چالش کد یک‌بارمصرف و کنترل نرخ","۹۰ روز"),
("identity","device","هویت","user_id, platform, push_token, trusted","دستگاه‌های ثبت‌شده و توکن اعلان","۱۸ ماه"),
("identity","role","هویت","code, title_fa","نقش‌های سیستمی","دائمی"),
("identity","permission","هویت","code, resource, action, scope","مجوزها با قالب resource:action:scope","دائمی"),
("identity","role_permission","هویت","role_code, permission_code","پیوند نقش و مجوز","دائمی"),
("identity","user_role","هویت","user_id, role_code, scope_type","تخصیص نقش با دامنه (کل/منطقه/سازمان)","۷ سال"),
("identity","org_account","هویت","legal_name, national_entity_id, credit_limit","حساب سازمانی B2B و اعتبار","۷ سال"),
("identity","org_member","هویت","org_id, user_id, org_role, approval_limit","کاربران سازمانی و سقف تأیید","۷ سال"),
("identity","consent_record","هویت","consent_type, version, granted_at","رضایت‌نامه‌های نسخه‌دار (موقعیت، ضبط، آموزش مدل)","۷ سال"),
("identity","provider_profile","متخصص","verified_level, status, tier, last_location, rates","هسته عرضه: سطح تأیید، رتبه، نرخ‌ها، موقعیت زنده","۷ سال"),
("identity","provider_org_link","متخصص","provider_id, org_id, revenue_share","اتصال متخصص به شرکت خدماتی","۷ سال"),
("identity","provider_document","متخصص","kind, file_id, ocr_data, expiry_date, status","مدارک هویتی و صلاحیت با چرخه انقضا","۷ سال"),
("identity","identity_verification","متخصص","face_match_score, liveness_score, tamper_score, decision","خروجی Face-Match و Liveness برای حسابرسی","۷ سال"),
("identity","skill_test_attempt","متخصص","category_id, score, passed, video_file_id","آزمون مهارت و ویدیو معرفی","۵ سال"),
("identity","provider_skill","متخصص","skill_code, level, verified_at","مهارت‌های تأییدشده (ورودی تطبیق)","۷ سال"),
("identity","provider_equipment","متخصص","equipment_code, quantity, verified_at","تجهیزات ثبت‌شده (ورودی تطبیق)","۷ سال"),
("identity","provider_service_area","متخصص","zone_id, polygon, radius_km","مناطق جغرافیایی فعالیت","۷ سال"),
("identity","provider_availability","متخصص","weekday, date_override, from_time, to_time","تقویم هفتگی و استثناها","۳ سال"),
("identity","provider_capacity","متخصص","date, slots_total, accepted_count","ظرفیت روزانه و کنترل هم‌زمانی","۳ سال"),
("identity","provider_sanction","متخصص","type, reason_code, from_date, to_date","تعلیق/جریمه با فرایند اعتراض","۷ سال"),
("identity","provider_tier_history","متخصص","tier, commission_rate, from_date","تاریخ تغییر سطح و کمیسیون","۷ سال"),
("catalog","service_category","کاتالوگ","slug, title_fa, parent_id, warranty_days_default","درخت دسته‌بندی خدمات","دائمی"),
("catalog","service_item","کاتالوگ","pricing_model, unit, required_skills, triage_flow_id","خدمت قابل سفارش و پیش‌نیازها","دائمی"),
("catalog","rate_card","کاتالوگ","version, region_id, org_id, effective_from","نسخه تعرفه به تفکیک منطقه/سازمان","۷ سال"),
("catalog","rate_card_item","کاتالوگ","service_item_id, base_price, min_price, max_price","اقلام تعرفه","۷ سال"),
("catalog","price_rule","کاتالوگ","kind, scope, formula, priority, valid_from","قواعد قیمت (Surge، پیچیدگی، تخفیف) به‌صورت داده","۷ سال"),
("catalog","demand_supply_metric","کاتالوگ","zone_id, window_start, demand_index, surge_factor","ورودی سری‌زمانی محاسبه Surge","۲۴ ماه"),
("catalog","price_quote","کاتالوگ","order_id, model, min/max/final_amount, breakdown, engine_version","تخمین قیمت قابل بازتولید و حسابرسی","۷ سال"),
("catalog","price_quote_revision","کاتالوگ","quote_id, reason_code, old/new_amount, approved_by_customer","تغییر قیمت (Change Order) و تأیید مشتری","۷ سال"),
("catalog","promotion","کاتالوگ","code, kind, value, scope, max_uses","کوپن و تخفیف با محدودیت ضدسوءاستفاده","۵ سال"),
("catalog","tax_rule","کاتالوگ","region_id, category_id, vat_rate, withholding_rate","نرخ‌های مالیاتی پارامتری و نسخه‌دار","۱۰ سال"),
("catalog","triage_flow","کاتالوگ","category_id, version, nodes, active","درخت پرسش تریاژ قابل ویرایش بی‌کد","دائمی"),
("ord","order","سفارش","code, customer_id, status, urgency, location, escrow_state","موجودیت مرکزی سفارش با وضعیت پرداخت و Escrow","۷ سال"),
("ord","order_media","سفارش","order_id, file_id, type, ai_tags, transcript","رسانه‌های ورودی و مستندات (EXIF حذف‌شده)","۱۸ ماه"),
("ord","order_triage_answer","سفارش","order_id, question_id, answer, confidence","پاسخ تریاژ و مبنای تخمین قیمت","۳ سال"),
("ord","order_item","سفارش","kind, title, qty, unit_price, total","اقلام صورتحساب (خدمت/مصالح/ایاب‌وذهاب)","۷ سال"),
("ord","order_checklist","سفارش","phase, item_code, required, photo_file_id, geo","چک‌لیست قبل/بعد با عکس الزامی","۷ سال"),
("ord","order_timeline","سفارش","order_id, event_code, actor_id, payload","خط زمانی نمایشی سفارش","۳ سال"),
("ord","order_event","سفارش","event_type, payload, correlation_id","رویدادهای سفارش (پارتیشن ماهانه، منبع Kafka)","۲۴ ماه"),
("ord","order_status_transition","سفارش","from_status, to_status, by_user, reason","تغییرات وضعیت برای حسابرسی","۷ سال"),
("ord","booking","سفارش","provider_id, time_range, check_in/out_at, gps_in/out","رزرو با قید عدم تعارض تقویم (EXCLUDE)","۷ سال"),
("ord","job_worklog","سفارش","booking_id, minutes_worked, materials_used","ثبت زمان و مصالح (مبنای تسویه ساعتی)","۷ سال"),
("ord","recurring_plan","سفارش","customer_id, org_id, cadence, next_run_at","سفارش دوره‌ای و یادآور سرویس","۳ سال"),
("ord","maintenance_contract","سفارش","org_id, sla_terms, monthly_fee, from/to_date","قرارداد نگهداری B2B","۷ سال"),
("dispatch","dispatch_request","تخصیص","order_id, mode, wave_no, radius_m, status","وضعیت زنده فرآیند تخصیص","۲۴ ماه"),
("dispatch","job_offer","تخصیص","provider_id, rank_score, score_breakdown, expires_at, response","پیشنهاد به متخصص با توضیح‌پذیری امتیاز","۲۴ ماه"),
("dispatch","assignment","تخصیص","order_id, provider_id, assigned_by, released_at","تخصیص قطعی با قید یکتایی تخصیص فعال","۷ سال"),
("dispatch","assignment_rejection","تخصیص","offer_id, reason_code","دلیل رد (ورودی یادگیری الگوریتم)","۲۴ ماه"),
("dispatch","dispatch_wave","تخصیص","wave_no, candidate_count, radius_m, outcome","موج‌های تشدید و نتیجه هر موج","۲۴ ماه"),
("dispatch","provider_score_snapshot","تخصیص","provider_id, components, total, context","عکس لحظه‌ای امتیاز برای بازتولید تصمیم","۲۴ ماه"),
("dispatch","emergency_request","تخصیص","order_id, eta_minutes, prepay_txn_id, safety_ack_at","درخواست اعزام فوری","۷ سال"),
("pay","payment_intent","پرداخت","amount, method, status, idempotency_key, psp_ref","تعهد پرداخت با کلید یکتایی","۷ سال"),
("pay","payment_txn","پرداخت","psp, psp_txn_id, amount, fee, raw_response","رکورد خام درگاه برای مغایرت‌یابی","۷ سال"),
("pay","ledger_account","دفتر کل","code, kind, owner_type/owner_id, normal_balance","حساب‌های دفتر کل (دوعاملی)","دائمی"),
("pay","ledger_entry","دفتر کل","txn_id, account_id, direction, amount, hash_prev","قیدهای دوعاملی غیرقابل‌ویرایش با زنجیره هش","۷ سال (پارتیشن ماهانه)"),
("pay","escrow_hold","پرداخت","order_id, amount, released/refunded_amount, status","نگهداشت مبلغ سفارش با قید یکتایی نگهداشت باز","۷ سال"),
("pay","milestone","پرداخت","order_id, seq, share_percent, condition, status","مرحله مالی پروژه با شرط آزادسازی","۷ سال"),
("pay","escrow_release","پرداخت","hold_id, milestone_id, amount, type, triggered_by","آزادسازی/بازگشت مبلغ با محرک مشخص","۷ سال"),
("pay","wallet","پرداخت","owner_type, owner_id, balance_cached","کیف پول مشتری/متخصص/سازمان (Cache از دفتر کل)","۷ سال"),
("pay","wallet_transaction","پرداخت","wallet_id, kind, amount, ref_type/ref_id","گردش کیف پول","۷ سال"),
("pay","payout_batch","تسویه","cycle_date, status, total_amount, approved_by","دوره تسویه دسته‌ای متخصصان","۷ سال"),
("pay","payout_item","تسویه","provider_id, iban_hash, gross/commission/tax/net","اقلام تسویه هر متخصص","۷ سال"),
("pay","invoice","مالی","number, order_id, org_id, vat, total, status","فاکتور عملیاتی و قانونی","۱۰ سال"),
("pay","tax_invoice","مالی","tax_id_unique, submit_status, response_code, retry_count","صف ارسال صورتحساب الکترونیکی (سامانه مؤدیان)","۱۰ سال"),
("pay","refund","پرداخت","payment_intent_id, amount, reason_code, status","بازگشت وجه با مسیر تأیید","۷ سال"),
("pay","bnpl_contract","مالی","partner, approved_amount, installment_count, schedule","قرارداد پرداخت اقساطی (ریسک با پارتنر)","۷ سال"),
("pay","reconciliation_run","مالی","run_date, source, matched, diff_count","اجرای روزانه مغایرت‌یابی","۷ سال"),
("pay","reconciliation_item","مالی","run_id, ref, expected, actual, status","ردیف‌های مغایرت","۷ سال"),
("trust","review","اعتماد","order_id, provider_id, text, moderation_status","نظر تأییدشده فقط برای سفارش اجراشده","۵ سال"),
("trust","review_dimension","اعتماد","review_id, dimension, score","امتیاز پنج‌بعدی نظر","۵ سال"),
("trust","review_appeal","اعتماد","review_id, reason, evidence, decision","اعتراض متخصص به نظر","۵ سال"),
("trust","quality_check","اعتماد","order_id, type, score, checklist, result","بازرسی کیفیت خودکار/انسانی","۵ سال"),
("trust","rework_case","اعتماد","order_id, symptom, root_cause, fix_cost","پرونده بازکاری و هزینه آن","۵ سال"),
("trust","warranty","اعتماد","order_id, category_id, expires_at, status","گارانتی فعال هر سفارش","۵ سال"),
("trust","insurance_policy","بیمه","partner_name, master_policy_no, premium_rate","بیمه‌نامه گروهی پلتفرم","۱۰ سال"),
("trust","insurance_coverage","بیمه","policy_id, category_id, max_amount, deductible","سقف پوشش به تفکیک دسته","۱۰ سال"),
("trust","claim","بیمه","order_id, type, amount_requested, evidence, status","پرونده ادعای خسارت","۷ سال"),
("trust","claim_assessment","بیمه","claim_id, method, decision, amount_approved","ارزیابی خودکار/کارشناسی خسارت","۷ سال"),
("trust","dispute","اعتماد","order_id, category, freeze_escrow, status","پرونده اختلاف و توقف Escrow","۷ سال"),
("trust","dispute_message","اعتماد","dispute_id, sender_role, body, attachments","مذاکرات پرونده اختلاف","۵ سال"),
("trust","dispute_resolution","اعتماد","dispute_id, decision, amounts, rationale","رأی نهایی و دلیل آن","۷ سال"),
("risk","risk_signal","ریسک","entity_type, entity_id, signal_code, value","سیگنال‌های خام ریسک","۲۴ ماه"),
("risk","risk_score","ریسک","scope, score, band, model_version, features","امتیاز ریسک قابل توضیح","۲۴ ماه"),
("risk","risk_case","ریسک","entity_type, band, status, assigned_to, decision","پرونده بررسی انسانی ریسک","۷ سال"),
("risk","entity_link","ریسک","link_type, weight, first/last_seen","گراف پیوند (دستگاه/شبا/آدرس/IP) برای کشف خوشه","۲۴ ماه"),
("risk","collusion_alert","ریسک","pair_hash, pattern, evidence, confidence","هشدار تبانی با تخمین زیان","۷ سال"),
("risk","blacklist_entry","ریسک","type, value_hash, reason, expires_at","لیست سیاه قابل اعتراض","۷ سال"),
("comms","conversation","ارتباطات","order_id, type, participants, last_message_at","گفتگوهای سفارش/پشتیبانی/بات","۱۸ ماه"),
("comms","message","ارتباطات","conversation_id, sender_role, body, redacted","پیام‌ها با امکان Redaction","۱۸ ماه"),
("comms","message_attachment","ارتباطات","message_id, file_id, kind, checksum","پیوست پیام","۱۸ ماه"),
("comms","masked_call","ارتباطات","virtual_number, direction, duration, recording_file_id","تماس با شماره پوشانده‌شده","۱۸ ماه"),
("comms","call_transcript","ارتباطات","call_id, text, keywords, pii_flags","رونویسی تماس (جست‌وجو و کشف تبانی)","۱۸ ماه"),
("comms","video_session","ارتباطات","stage, participants, recording_file_id, notes","بازدید ویدیویی و جلسه کارشناسی","۱۸ ماه"),
("comms","support_ticket","پشتیبانی","code, channel, priority, level, sla_due_at","تیکت با SLA و مسیر تشدید","۵ سال"),
("comms","ticket_event","پشتیبانی","ticket_id, type, from/to_value, actor_id","تاریخچه اقدامات تیکت","۵ سال"),
("comms","knowledge_article","پشتیبانی","slug, audience, version, body","پایه دانش مشترک سایت/بات/پشتیبانی","دائمی"),
("comms","notification_template","اطلاع‌رسانی","code, channel, locale, priority, active","قالب اعلان چندکاناله","دائمی"),
("comms","notification_log","اطلاع‌رسانی","user_id, channel, status, delivered_at","لاگ ارسال (پارتیشن ماهانه)","۱۸ ماه"),
("comms","notification_preference","اطلاع‌رسانی","user_id, channel, enabled, quiet_hours","ترجیحات اعلان و ساعات مجاز","۳ سال"),
("comms","crm_segment","CRM","name, rules, size_cached","بخش‌بندی مشتری برای کمپین","۳ سال"),
("comms","campaign","CRM","segment_id, channel, template_code, status","کمپین‌ها و بودجه","۵ سال"),
("ai","ai_inference","هوش مصنوعی","model_name, purpose, input_ref, output, confidence","ردیابی همه استنتاج‌ها (پارتیشن ماهانه)","۱۸ ماه"),
("ai","ai_label","هوش مصنوعی","input_ref, inference_id, label, labeler_type","برچسب انسانی/خودکار برای آموزش مدل","۳ سال"),
("platform","provider_location_ping","پلتفرم","provider_id, location, is_mock, order_id","پینگ موقعیت (پارتیشن ماهانه)","۹۰ روز"),
("platform","feature_flag","پلتفرم","key, rules, rollout_percent, kill_switch","فلگ قابلیت و خاموش‌سازی اضطراری","دائمی"),
("platform","experiment_assignment","پلتفرم","flag_key, user_id, variant","تخصیص آزمایش A/B","۳ سال"),
("platform","audit_log","پلتفرم","actor_id, action, resource_type, before/after_data, correlation_id","لاگ حسابرسی جامع (پارتیشن ماهانه)","۷ سال"),
("platform","idempotency_key","پلتفرم","key, scope, request_hash, response","کنترل تکرار عملیات نوشتنی","۷ روز"),
("analytics","analytics_daily_agg","تحلیل","date, zone_id, category_id, orders, gmv, take_rate","پیش‌تجمیع روزانه داشبوردها","۵ سال"),
]

def write_data_dictionary():
    dst = os.path.join(ROOT, 'db', 'data_dictionary.csv')
    with open(dst, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['خوشه (Schema)', 'جدول', 'دامنه', 'ستون‌های کلیدی', 'توضیح کارکردی', 'نگه‌داری/حذف'])
        for r in ROWS:
            w.writerow(r)
    print("WROTE", dst, f"({len(ROWS)} tables)")

# ---------------------------------------------------------------- XLSX
HDR_FILL = PatternFill("solid", fgColor="0B3F4D")
SUB_FILL = PatternFill("solid", fgColor="E6F4F2")
TOT_FILL = PatternFill("solid", fgColor="FFF4E6")
THIN = Side(style="thin", color="D9DEE4")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HDR_FONT = Font(bold=True, color="FFFFFF", size=11)
BOLD = Font(bold=True)

def style_header(ws, row=1, ncols=1):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = HDR_FILL; cell.font = HDR_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER
    ws.row_dimensions[row].height = 30

def style_body(ws, first, last, ncols, wrap=True):
    for r in range(first, last + 1):
        for c in range(1, ncols + 1):
            cell = ws.cell(row=r, column=c)
            cell.alignment = Alignment(horizontal="right", vertical="top", wrap_text=wrap)
            cell.border = BORDER

def widths(ws, ws_widths):
    for i, w in enumerate(ws_widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

def build_xlsx():
    wb = Workbook()

    # ---------- Sheet 1: برآورد کار
    ws = wb.active; ws.title = "برآورد کار"
    ws.sheet_view.rightToLeft = True
    headers = ["ماژول", "نفر-ماه (استاندارد)", "نفر-ماه (حداقلی)", "ریسک برآورد", "فاز اصلی", "توضیح"]
    ws.append(headers); style_header(ws, 1, len(headers))
    data = [
      ("هویت، ورود، RBAC، چرخه عمر کاربر", 4, 2.5, "کم", "۱", "الگوهای شناخته‌شده؛ ریسک Passkey روی دستگاه‌های قدیمی"),
      ("Onboarding و احراز متخصص (OCR/Face)", 5, 3, "متوسط", "۱", "وابسته به کیفیت سرویس OCR/Liveness"),
      ("کاتالوگ، تریاژ، قیمت‌گذاری پویا", 6, 3.5, "متوسط", "۱–۲", "نسخه‌بندی Rate Card و قواعد داده‌محور"),
      ("سفارش، تقویم و اجرا", 7, 4, "متوسط", "۱", "چرخه وضعیت + مستندسازی تصویری + آفلاین"),
      ("موتور تخصیص و اعزام فوری", 7, 4, "بالا", "۱–۲", "نسخه ساده در MVP، نسخه کامل با داده واقعی در فاز ۲"),
      ("پرداخت، کیف پول، Escrow، Milestone", 8, 4.5, "بالا", "۱ و ۳", "حساس‌ترین بخش؛ تأیید دوعاملی و مغایرت‌یابی"),
      ("فاکتور، مالیات، B2B، BNPL", 5, 2.5, "متوسط–بالا", "۳", "وابسته به API سامانه مؤدیان و پارتنر BNPL"),
      ("اعتماد: امتیاز، گارانتی، بیمه، اختلاف", 5, 3, "متوسط", "۱–۲", "شامل پرونده ادعا و داوری"),
      ("ضدتقلب و ریسک", 4, 2, "متوسط", "۲–۳", "قاعده‌محور در MVP، مدل گراف در فاز ۳"),
      ("پشتیبانی: چت، تماس امن، ویدیو، تیکت، بات", 8, 4.5, "متوسط", "۲–۳", "WebRTC و ضبط جلسه نیازمند زیرساخت رسانه"),
      ("هوش مصنوعی ورودی و مدل‌های تحلیلی", 6, 2, "بالا", "۲", "کیفیت ASR فارسی و داده برچسب‌خورده اولیه"),
      ("پنل‌های عملیات، ادمین، مالی و B2B", 8, 4.5, "متوسط", "۱–۳", "حجم زیاد صفحه با ریسک کم؛ موازی‌پذیر"),
      ("اپ مشتری و اپ متخصص", 10, 6, "متوسط", "۱–۲", "دو اپ روی کدبیس مشترک"),
      ("زیرساخت، CI/CD، پایش، امنیت، DR", 6, 3, "کم–متوسط", "۰–۴", "توزیع‌شده در طول پروژه"),
      ("مدیریت پروژه، QA، مستندسازی", 8, 4, "کم", "۰–۴", "۱۵–۲۰٪ ظرفیت کل"),
    ]
    for row in data: ws.append(row)
    last = ws.max_row
    ws.append(["جمع", f"=SUM(B2:B{last})", f"=SUM(C2:C{last})", "—", "—", "معادل حدود ۴۲۰ نفر-هفته کار در سناریوی استاندارد"])
    style_body(ws, 2, ws.max_row, 6)
    for c in range(1, 7):
        ws.cell(row=ws.max_row, column=c).fill = TOT_FILL
        ws.cell(row=ws.max_row, column=c).font = BOLD
    widths(ws, [40, 20, 18, 14, 10, 52]); ws.freeze_panes = "A2"

    # ---------- Sheet 2: سناریوهای هزینه
    ws2 = wb.create_sheet("سناریوهای هزینه"); ws2.sheet_view.rightToLeft = True
    h2 = ["سناریو", "دامنه", "مدت (ماه)", "ظرفیت (نفر-ماه)", "حداقل (میلیون تومان)", "حداکثر (میلیون تومان)", "مناسب برای"]
    ws2.append(h2); style_header(ws2, 1, len(h2))
    s2 = [
      ("حداقلی / اعتبارسنجی بازار", "فاز ۱ + بخشی از فاز ۲", "۷–۹", 35, 14000, 24000, "استارتاپ با بودجه محدود، یک شهر، اثبات محصول"),
      ("استاندارد (پیشنهاد شده)", "فاز ۰ تا ۳", "۱۰–۱۲", 75, 30000, 45000, "رقابت جدی در چند شهر"),
      ("تسریع‌شده / پیشتاز", "فاز ۰ تا ۴، موازی‌سازی کامل", "۷–۸", 75, 45000, 65000, "بودجه بالا و پنجره رقابتی بسته"),
    ]
    for r in s2: ws2.append(r)
    ws2.append(["یادداشت", "ارقام برنامه‌ریزی‌محور و وابسته به نرخ روز تیم است؛ پیش از قرارداد باید تأیید شود.", "", "", "", "", "مالکیت کد و داده ۱۰۰٪ کارفرما + دوره تضمین ۶ ماهه"])
    style_body(ws2, 2, ws2.max_row, 7)
    ws2.cell(row=ws2.max_row, column=1).font = BOLD
    widths(ws2, [26, 28, 12, 16, 20, 20, 46]); ws2.freeze_panes = "A2"
    for r in range(2, ws2.max_row):
        ws2.cell(row=r, column=5).number_format = "#,##0"
        ws2.cell(row=r, column=6).number_format = "#,##0"

    # ---------- Sheet 3: هزینه جاری
    ws3 = wb.create_sheet("هزینه جاری ماهانه"); ws3.sheet_view.rightToLeft = True
    h3 = ["قلم هزینه", "حداقل (میلیون تومان/ماه)", "حداکثر (میلیون تومان/ماه)", "توضیح"]
    ws3.append(h3); style_header(ws3, 1, 4)
    s3 = [
      ("محاسبات، DB، Redis، ذخیره‌سازی", 800, 1500, "۶–۱۰ سرویس روی K8s؛ ذخیره رسانه ۲–۵ ترابایت"),
      ("CDN، WAF، دامنه", 150, 400, "ترافیک تصویری سنگین"),
      ("Kafka/OpenSearch/ClickHouse", 300, 800, "وابسته به نگهداری داده و حجم رویداد"),
      ("پیامک OTP و اطلاع‌رسانی", 200, 600, "۵۰۰ هزار کاربر ماهانه، ۲ پیامک/کاربر"),
      ("Push و کانال جایگزین", 50, 200, "پایداری در اختلال"),
      ("ASR/OCR/Face-Match/تحلیل", 400, 1200, "وابسته به حجم دقیقه صدا و تعداد عکس"),
      ("نقشه، مسیریابی، ETA", 100, 350, "سرویس داخلی یا تجاری"),
      ("VoIP و ضبط تماس", 100, 300, "بر پایه دقیقه تماس پوشانده‌شده"),
      ("پایش، خطا، لاگ", 80, 250, "خودمیزبانی ارزان‌تر است"),
      ("(متغیر) کارمزد درگاه پرداخت", 0, 0, "۰.۵–۱٪ مبلغ تراکنش — درصدی"),
      ("(متغیر) حق بیمه و خسارت", 0, 0, "۰.۵–۱.۵٪ GMV — درصدی"),
    ]
    for r in s3: ws3.append(r)
    last3 = ws3.max_row
    ws3.append(["جمع ثابت ماهانه", f"=SUM(B2:B{last3})", f"=SUM(C2:C{last3})", "≈ ۳ تا ۶ میلیارد تومان در ماه (سال اول)"])
    style_body(ws3, 2, ws3.max_row, 4)
    for c in range(1, 5):
        ws3.cell(row=ws3.max_row, column=c).fill = TOT_FILL
        ws3.cell(row=ws3.max_row, column=c).font = BOLD
    widths(ws3, [36, 22, 22, 52]); ws3.freeze_panes = "A2"

    # ---------- Sheet 4: تیم
    ws4 = wb.create_sheet("تیم پیشنهادی"); ws4.sheet_view.rightToLeft = True
    h4 = ["نقش", "تعداد", "حضور", "مسئولیت اصلی"]
    ws4.append(h4); style_header(ws4, 1, 4)
    s4 = [
      ("معمار راهکار / Tech Lead", 1, "تمام‌وقت", "معماری، قرارداد سرویس، بازبینی کد بحرانی"),
      ("Product Owner + Business Analyst", 1, "تمام‌وقت", "اولویت‌بندی و قواعد کسب‌وکار"),
      ("طراح UX/UI", 1, "تمام‌وقت (فاز ۰–۲)", "وایرفریم، دیزاین‌سیستم، آزمون کاربری"),
      ("توسعه‌دهنده موبایل (Flutter)", 2, "تمام‌وقت", "اپ مشتری و متخصص"),
      ("توسعه‌دهنده بک‌اند", 4, "تمام‌وقت", "Identity, Catalog, Order, Dispatch, Payments"),
      ("توسعه‌دهنده فرانت‌وب", 2, "تمام‌وقت", "پنل‌های عملیات/ادمین/B2B و سایت"),
      ("مهندس داده/ML", 1, "تمام‌وقت (فاز ۲+)", "AI Intake، مدل ریسک و ETA، خط لوله داده"),
      ("مهندس DevOps/SRE", 1, "تمام‌وقت", "K8s، CI/CD، پایش، DR"),
      ("مهندس QA", 2, "تمام‌وقت", "تست قرارداد، رگرسیون، بار، UAT"),
      ("کارشناس امنیت", 0.5, "پاره‌وقت ماهانه", "تست نفوذ، بازبینی دسترسی، انطباق"),
      ("SME دامنه/پشتیبانی", 0.5, "پاره‌وقت", "سناریو پشتیبانی، Playbook، برچسب‌گذاری داده"),
    ]
    for r in s4: ws4.append(r)
    ws4.append(["جمع نفرات مؤثر", f"=SUM(B2:B{ws4.max_row})", "—", "حداقل تیم قابل قبول فاز ۱: ۷ نفر تمام‌وقت مؤثر"])
    style_body(ws4, 2, ws4.max_row, 4)
    for c in range(1, 5):
        ws4.cell(row=ws4.max_row, column=c).fill = TOT_FILL
        ws4.cell(row=ws4.max_row, column=c).font = BOLD
    widths(ws4, [34, 8, 20, 52]); ws4.freeze_panes = "A2"

    # ---------- Sheet 5: مراحل پرداخت
    ws5 = wb.create_sheet("مراحل پرداخت"); ws5.sheet_view.rightToLeft = True
    h5 = ["مرحله", "سهم پرداخت", "معیار پذیرش"]
    ws5.append(h5); style_header(ws5, 1, 3)
    s5 = [
      ("شروع و فاز ۰", "۱۰٪", "تصویب معماری، آماده‌سازی محیط‌ها، CI/CD فعال"),
      ("پایان فاز ۱ (MVP)", "۲۵٪", "اجرای UAT بحرانی، Fulfillment ≥ ۷۵٪، خطای پرداخت < ۲٪"),
      ("پایان فاز ۲ (هوشمندسازی)", "۲۵٪", "دقت دسته‌بندی ≥ ۸۵٪، TTA < ۳ دقیقه، پنل ریسک فعال"),
      ("پایان فاز ۳ (فین‌تک/B2B)", "۲۰٪", "دفتر کل متوازن، فاکتور مالیاتی موفق، یک قرارداد B2B واقعی"),
      ("پایان فاز ۴ و تحویل نهایی", "۱۵٪", "پایداری ۹۹.۹٪ در ۳۰ روز، انتقال دانش، تمرین DR"),
      ("نگهداشت سالانه (جدا)", "۱۰–۱۸٪ هزینه ساخت", "SLA پاسخ ۱۵ دقیقه سطح ۱ و رفع بحرانی ۴ ساعت"),
    ]
    for r in s5: ws5.append(r)
    style_body(ws5, 2, ws5.max_row, 3)
    widths(ws5, [30, 22, 62]); ws5.freeze_panes = "A2"

    dst = os.path.join(ROOT, 'rfp', 'estimate.xlsx')
    wb.save(dst)
    print("WROTE", dst)

if __name__ == "__main__":
    write_data_dictionary()
    build_xlsx()
