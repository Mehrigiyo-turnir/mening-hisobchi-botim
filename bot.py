"""Telegram bot for recording income and expenses - Postgres version + Voice + Delete."""

import csv
import logging
import os
import re
import sys
import tempfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from flask import Flask
import threading

# --- FLASK - UPTIMEROBOT UCHUN ---
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "Bot ishlayapti!"

@app_flask.route('/health')
def health():
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host='0.0.0.0', port=port)

threading.Thread(target=run_flask, daemon=True).start()

# --- TELEGRAM ---
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Postgres uchun
DATABASE_URL = os.getenv("DATABASE_URL")
USE_DB = bool(DATABASE_URL)
conn = None
if USE_DB:
    import psycopg2
    import psycopg2.extras

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

FAYL = Path(__file__).resolve().with_name("hisobot.csv")
TOSHKENT = ZoneInfo("Asia/Tashkent")

HISOBOT_TUGMALARI = {
    "📅 Bugungi hisobot": "bugun",
    "📆 Kechagi hisobot": "kecha",
    "📊 Haftalik hisobot": "hafta",
    "🗓 Oylik hisobot": "oy",
    "📈 Yillik hisobot": "yil",
}
DAVR_CALLBACK_TOKENLARI = {"bugun": "1d","kecha": "yesterday","hafta": "7d","oy": "month","yil": "year"}
CALLBACK_TOKEN_DAVRLARI = {token: davr for davr, token in DAVR_CALLBACK_TOKENLARI.items()}
CALLBACK_TURI_NOMLARI = {"expense": "CHIQIM","income": "KIRIM"}
DAVR_BATAFSIL_SARLAVHALARI = {"bugun": "Kunlik","kecha": "Kechagi","hafta": "Haftalik","oy": "Oylik","yil": "Yillik"}
ESKI_BATAFSIL_CALLBACKLAR = {
    "detail_bugungi": ("bugun", "CHIQIM"),"detail_kechagi": ("kecha", "CHIQIM"),
    "detail_haftalik": ("hafta", "CHIQIM"),"detail_oylik": ("oy", "CHIQIM"),"detail_yillik": ("yil", "CHIQIM"),
}
HISOBOT_KLAVIATURASI = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton("📅 Bugungi hisobot"),KeyboardButton("📆 Kechagi hisobot")],
              [KeyboardButton("📊 Haftalik hisobot"),KeyboardButton("🗓 Oylik hisobot")],
              [KeyboardButton("📈 Yillik hisobot")]], resize_keyboard=True, one_time_keyboard=False, is_persistent=True)
HISOBOT_TUGMASI_FILTERI = filters.Regex(r"^(?:" + "|".join(re.escape(t) for t in HISOBOT_TUGMALARI) + r")$")

def get_db_conn():
    if not USE_DB: return None
    return psycopg2.connect(DATABASE_URL)

def csv_faylni_tayyorla() -> None:
    if USE_DB:
        try:
            c = get_db_conn(); cur = c.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS hisobot (
                id SERIAL PRIMARY KEY, sana DATE, turi TEXT, summa BIGINT, matn TEXT, vaqt TEXT)""")
            c.commit(); cur.close(); c.close()
            logger.info("Postgres tayyor!")
        except Exception as e:
            logger.error(f"DB xato: {e}")
        return
    if not FAYL.exists() or FAYL.stat().st_size == 0:
        with FAYL.open("w", newline="", encoding="utf-8") as f: csv.writer(f).writerow(["sana","turi","summa","matn","vaqt"])

def sanani_top(matn: str, bugun: date | None = None) -> str:
    matn = matn.lower(); bugun = bugun or datetime.now(TOSHKENT).date()
    if re.search(r"\bkecha\b", matn): return (bugun - timedelta(days=1)).isoformat()
    kunlar = re.search(r"\b(\d+)\s*kun\s+oldin\b", matn)
    if kunlar: return (bugun - timedelta(days=int(kunlar.group(1)))).isoformat()
    return bugun.isoformat()

def sanani_korsatish(sana: date | str) -> str:
    if isinstance(sana, str): sana = date.fromisoformat(sana)
    return sana.strftime("%d.%m.%Y")

def summa_top(matn: str) -> tuple[int, str]:
    kichik = matn.lower().replace("’", "'").replace("‘", "'").replace("ʻ", "'").replace("ʼ", "'")
    million = re.search(r"(\d+(?:[.,]\d+)?)\s*mln\b", kichik)
    ming = re.search(r"(\d+)\s*ming\b", kichik)
    oddiy = re.search(r"\b(\d{4,})\b", kichik)
    if million: summa = int(Decimal(million.group(1).replace(",", ".")) * 1_000_000)
    elif ming: summa = int(ming.group(1)) * 1000
    elif oddiy: summa = int(oddiy.group(1))
    else: summa = 0
    turi = "KIRIM" if re.search(r"\bkrim\b", kichik) else "CHIQIM"
    return summa, turi

def egasimi(update: Update) -> bool:
    user = update.effective_user; chat = update.effective_chat
    owner_id = os.getenv("TELEGRAM_OWNER_ID", "").strip()
    if user is None or chat is None or chat.type!= ChatType.PRIVATE or not owner_id: return False
    try: return user.id == int(owner_id)
    except ValueError: return False

async def shaxsiyligini_ayt(update: Update) -> None:
    m = update.effective_message
    if m is not None: await m.reply_text("Bu bot shaxsiy", reply_markup=ReplyKeyboardRemove())

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    m = update.effective_message; user = update.effective_user; chat = update.effective_chat
    if m is None or user is None: return
    if chat is not None and chat.type!= ChatType.PRIVATE:
        await m.reply_text("ID raqamingizni olish uchun botga shaxsiy xabar yuboring."); return
    await m.reply_text(f"Telegram ID raqamingiz: {user.id}", reply_markup=(HISOBOT_KLAVIATURASI if egasimi(update) else ReplyKeyboardRemove()))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    m = update.effective_message
    if m is None: return
    if not egasimi(update): await shaxsiyligini_ayt(update); return
    await m.reply_text("Kirim-chiqim botiga xush kelibsiz!\nMasalan: bugun 50 ming tushlik qildim\nYoki: kecha krim 2 mln maosh keldi\n/hisobot — jami hisobotni ko'rish\n\n🎤 Ovozli xabar bilan ham yozishingiz mumkin!\n🗑 O'chirish uchun: `50 ming tushlik o'chir` deb yozing", reply_markup=HISOBOT_KLAVIATURASI)

# ================= YANGI: O'CHIRISH FUNKSIYASI =================
def yozuvni_ochir_qidirib(qidiruv_matni: str) -> tuple[bool, str]:
    if not qidiruv_matni:
        return False, "O'chirish uchun matn topilmadi"
    qidiruv_matni = qidiruv_matni.lower().strip()
    if USE_DB:
        try:
            c = get_db_conn()
            cur = c.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute("SELECT id, matn, summa, turi FROM hisobot ORDER BY id DESC")
            rows = cur.fetchall()
            for r in rows:
                if qidiruv_matni in (r["matn"] or "").lower():
                    cur.execute("DELETE FROM hisobot WHERE id = %s", (r["id"],))
                    c.commit()
                    cur.close()
                    c.close()
                    return True, f"{r['turi']} {int(r['summa']):,} so'm - {r['matn']}"
            cur.close()
            c.close()
            return False, f"'{qidiruv_matni}' ga o'xshash yozuv topilmadi"
        except Exception as e:
            logger.error(f"O'chirishda xato: {e}")
            return False, str(e)
    else:
        try:
            if not FAYL.exists(): return False, "Fayl yo'q"
            with FAYL.open("r", newline="", encoding="utf-8") as f:
                qatorlar = list(csv.DictReader(f))
            topilgan_index = -1
            for i in range(len(qatorlar)-1, -1, -1):
                if qidiruv_matni in (qatorlar[i].get("matn") or "").lower():
                    topilgan_index = i
                    break
            if topilgan_index == -1: return False, f"'{qidiruv_matni}' topilmadi"
            ochirilgan = qatorlar.pop(topilgan_index)
            with FAYL.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["sana","turi","summa","matn","vaqt"])
                writer.writeheader()
                writer.writerows(qatorlar)
            return True, f"{ochirilgan['turi']} {int(ochirilgan['summa']):,} so'm - {ochirilgan['matn']}"
        except Exception as e:
            return False, str(e)

async def ochirish_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    m = update.effective_message
    if m is None or m.text is None: return
    if not egasimi(update): await shaxsiyligini_ayt(update); return
    past = m.text.lower().strip()
    qidiruv = ""
    if past.endswith("o'chir"): qidiruv = m.text[:-5].strip()
    elif past.endswith("o‘chir"): qidiruv = m.text[:-5].strip()
    elif past.endswith("o’chir"): qidiruv = m.text[:-5].strip()
    elif past.endswith("ochir"): qidiruv = m.text[:-5].strip()
    elif past.endswith("o'chir."): qidiruv = m.text[:-6].strip()
    else: return

    if not qidiruv:
        await m.reply_text("Qaysi yozuvni o'chirishni yozmadingiz. Masalan:\n`bugun 50 ming tushlik o'chir`", reply_markup=HISOBOT_KLAVIATURASI)
        return
    muvaffaqiyat, info = yozuvni_ochir_qidirib(qidiruv)
    if muvaffaqiyat:
        await m.reply_text(f"🗑 O'chirildi:\n{info}", reply_markup=HISOBOT_KLAVIATURASI)
    else:
        await m.reply_text(f"❌ O'chirilmadi: {info}", reply_markup=HISOBOT_KLAVIATURASI)

async def yoz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    m = update.effective_message
    if m is None or m.text is None: return
    if not egasimi(update): await shaxsiyligini_ayt(update); return
    summa, turi = summa_top(m.text)
    if summa <= 0:
        past = m.text.lower()
        if "hisobot" in past:
            if "bugun" in past: await hisobotni_yubor(update, "bugun")
            elif "kecha" in past: await hisobotni_yubor(update, "kecha")
            elif "hafta" in past: await hisobotni_yubor(update, "hafta")
            elif "oy" in past: await hisobotni_yubor(update, "oy")
            elif "yil" in past: await hisobotni_yubor(update, "yil")
            else: await hisobotni_yubor(update, "jami")
            return
        await m.reply_text("Summani tushunmadim. Masalan: bugun 50 ming ketdi", reply_markup=HISOBOT_KLAVIATURASI); return
    now = datetime.now(TOSHKENT); sana = sanani_top(m.text, now.date()); vaqt = now.strftime("%H:%M")
    try:
        if USE_DB:
            c = get_db_conn(); cur = c.cursor()
            cur.execute("INSERT INTO hisobot (sana,turi,summa,matn,vaqt) VALUES (%s,%s,%s,%s,%s)", (sana,turi,summa,m.text,vaqt))
            c.commit(); cur.close(); c.close()
        else:
            with FAYL.open("a", newline="", encoding="utf-8") as f: csv.writer(f).writerow([sana,turi,summa,m.text,vaqt])
    except Exception:
        logger.exception("Saqlashda xato"); await m.reply_text("Yozuvni saqlashda xatolik.", reply_markup=HISOBOT_KLAVIATURASI); return
    await m.reply_text(f"✅ {sanani_korsatish(sana)} | {turi} {summa:,} so'm saqlandi\n📝 {m.text}", reply_markup=HISOBOT_KLAVIATURASI)

def hisobot_oraligini_top(davr: str) -> tuple[date | None, date | None, str]:
    bugun = datetime.now(TOSHKENT).date()
    if davr == "bugun": return bugun, bugun, f"Bugungi hisobot ({sanani_korsatish(bugun)})"
    if davr == "kecha": kecha = bugun - timedelta(days=1); return kecha, kecha, f"Kechagi hisobot ({sanani_korsatish(kecha)})"
    if davr == "hafta": bosh = bugun - timedelta(days=6); return bosh, bugun, f"Haftalik hisobot ({sanani_korsatish(bosh)} — {sanani_korsatish(bugun)})"
    if davr == "oy": bosh = bugun.replace(day=1); return bosh, bugun, f"Oylik hisobot ({sanani_korsatish(bosh)} — {sanani_korsatish(bugun)})"
    if davr == "yil": bosh = bugun.replace(month=1, day=1); return bosh, bugun, f"Yillik hisobot ({sanani_korsatish(bosh)} — {sanani_korsatish(bugun)})"
    if davr == "jami": return None, None, "Umumiy hisobot"
    raise ValueError(f"Noma'lum davr: {davr}")

def davr_yozuvlarini_oqi(boshlanish: date | None, tugash: date | None) -> list[dict[str, object]]:
    if USE_DB:
        try:
            c = get_db_conn(); cur = c.cursor(cursor_factory=psycopg2.extras.DictCursor)
            if boshlanish and tugash: cur.execute("SELECT * FROM hisobot WHERE sana BETWEEN %s AND %s", (boshlanish, tugash))
            else: cur.execute("SELECT * FROM hisobot")
            rows = cur.fetchall(); cur.close(); c.close()
            res = []
            for r in rows:
                if r["turi"] not in ("KIRIM","CHIQIM"): continue
                res.append({"sana": r["sana"], "vaqt": r["vaqt"] or "", "turi": r["turi"], "summa": int(r["summa"]), "matn": r["matn"] or ""})
            return res
        except Exception as e:
            logger.error(f"DB o'qishda xato: {e}"); return []
    with FAYL.open("r", newline="", encoding="utf-8") as f:
        qatorlar = csv.DictReader(f); tanlangan = []
        for q in qatorlar:
            try: sana = date.fromisoformat(q["sana"]); summa = int(q["summa"]); turi = q["turi"]
            except: continue
            if turi not in ("KIRIM","CHIQIM"): continue
            if boshlanish is not None and sana < boshlanish: continue
            if tugash is not None and sana > tugash: continue
            tanlangan.append({"sana": sana,"vaqt": (q.get("vaqt") or "").strip(),"turi": turi,"summa": summa,"matn": q.get("matn") or ""})
        return tanlangan

def hisobot_inline_klaviaturasi(davr: str):
    tokeni = DAVR_CALLBACK_TOKENLARI.get(davr)
    if tokeni is None: return None
    return InlineKeyboardMarkup([[InlineKeyboardButton("📋 Nimalarga sarfladim?", callback_data=f"report-detail-expense:{tokeni}"), InlineKeyboardButton("💰 Kirimlar", callback_data=f"report-detail-income:{tokeni}")]])

async def hisobotni_yubor(update: Update, davr: str) -> None:
    m = update.effective_message
    if m is None: return
    if not egasimi(update): await shaxsiyligini_ayt(update); return
    boshlanish, tugash, sarlavha = hisobot_oraligini_top(davr)
    try:
        yozuvlar = davr_yozuvlarini_oqi(boshlanish, tugash)
        kirim = sum(q["summa"] for q in yozuvlar if q["turi"] == "KIRIM")
        chiqim = sum(q["summa"] for q in yozuvlar if q["turi"] == "CHIQIM")
    except Exception:
        logger.exception("Hisobot xato"); await m.reply_text("Hisobotni o'qishda xatolik.", reply_markup=HISOBOT_KLAVIATURASI); return
    inline = hisobot_inline_klaviaturasi(davr)
    await m.reply_text(f"{sarlavha}\nKirim: {kirim:,} so'm\nChiqim: {chiqim:,} so'm\nQoldiq: {kirim - chiqim:,} so'm", reply_markup=inline or HISOBOT_KLAVIATURASI)

def tranzaksiya_izohi(matn: str) -> str:
    izoh = re.sub(r"\b\d+\s*kun\s+oldin\b|\b(?:bugun|kecha)\b"," ",matn,flags=re.IGNORECASE)
    izoh = re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:mln|ming)\b|\b\d{4,}\b"," ",izoh,flags=re.IGNORECASE)
    izoh = re.sub(r"\b(?:ketdi|sarfl\w*|to[’‘ʻʼ']ladim|oldim|qildim|topdim|keldi|tushdi)\b"," ",izoh,flags=re.IGNORECASE)
    return " ".join(izoh.split()).strip(" -–—") or "Izoh yo‘q"

def batafsil_hisobot_matni(davr: str, turi: str) -> str:
    boshlanish, tugash, _ = hisobot_oraligini_top(davr)
    if boshlanish is None or tugash is None: raise ValueError("Davr kerak")
    yozuvlar = [q for q in davr_yozuvlarini_oqi(boshlanish, tugash) if q["turi"] == turi]
    yozuvlar.sort(key=lambda q: (q["sana"], q["vaqt"] or "00:00"))
    if not yozuvlar: return f"Bu davrda {('kirim' if turi=='KIRIM' else 'chiqim')} bo'lmagan"
    tur_nomi = "kirimlar" if turi == "KIRIM" else "chiqimlar"
    sarlavha = f"{DAVR_BATAFSIL_SARLAVHALARI[davr]} {tur_nomi}:"
    satrlar = [sarlavha]
    for q in yozuvlar:
        vaqt = q["vaqt"] if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", q["vaqt"]) else "--:--"
        belgi = "💰" if turi=="KIRIM" else "💸"; ishora = "+" if turi=="KIRIM" else "-"
        satrlar.append(f"{belgi} {sanani_korsatish(q['sana'])} {vaqt} {ishora}{abs(q['summa']):,} so'm - {tranzaksiya_izohi(q['matn'])}")
    return "\n".join(satrlar)

def matnni_telegramga_bol(matn: str, chegara: int = 3800) -> list[str]:
    qismlar = []; joriy = ""
    for satr in matn.splitlines():
        if len(satr) > chegara:
            if joriy: qismlar.append(joriy); joriy=""
            for i in range(0,len(satr),chegara): qismlar.append(satr[i:i+chegara])
            continue
        yangi = len(joriy) + len(satr) + (1 if joriy else 0)
        if joriy and yangi > chegara: qismlar.append(joriy); joriy=satr
        else: joriy = f"{joriy}\n{satr}" if joriy else satr
    if joriy: qismlar.append(joriy)
    return qismlar

async def batafsil_hisobot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None: return
    if not egasimi(update): await query.answer("Bu bot shaxsiy", show_alert=True); await shaxsiyligini_ayt(update); return
    callback_data = query.data or ""
    if callback_data in ESKI_BATAFSIL_CALLBACKLAR: davr, turi = ESKI_BATAFSIL_CALLBACKLAR[callback_data]
    else:
        prefix, sep, tokeni = callback_data.partition(":"); kategoriya = prefix.removeprefix("report-detail-")
        davr = CALLBACK_TOKEN_DAVRLARI.get(tokeni) if sep else None; turi = CALLBACK_TURI_NOMLARI.get(kategoriya)
    if davr is None or turi is None: await query.answer("Hisobot davri topilmadi.", show_alert=True); return
    await query.answer(); m = update.effective_message
    if m is None: return
    try: qismlar = matnni_telegramga_bol(batafsil_hisobot_matni(davr, turi))
    except Exception: await m.reply_text("Batafsil hisobotda xatolik.", reply_markup=HISOBOT_KLAVIATURASI); return
    for i,qism in enumerate(qismlar): await m.reply_text(qism, reply_markup=HISOBOT_KLAVIATURASI if i==0 else None)

async def hisobot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not egasimi(update): await shaxsiyligini_ayt(update); return
    args = context.args
    if not args: await hisobotni_yubor(update, "jami"); return
    davr = {"bugun":"bugun","kecha":"kecha","haftalik":"hafta","oylik":"oy","yillik":"yil"}.get(args[0].lower())
    if davr is None or len(args)>1:
        m = update.effective_message
        if m is not None: await m.reply_text("Foydalanish: /hisobot [bugun|kecha|haftalik|oylik|yillik]", reply_markup=HISOBOT_KLAVIATURASI)
        return
    await hisobotni_yubor(update, davr)

async def menyu_hisoboti(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    m = update.effective_message
    if m is None or m.text is None: return
    davr = HISOBOT_TUGMALARI.get(m.text)
    if davr is not None: await hisobotni_yubor(update, davr)

# ================= OVOZLI FUNKSIYA - TIMEOUT TUZATILDI =================
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    m = update.effective_message
    if m is None: return
    if not egasimi(update): await shaxsiyligini_ayt(update); return
    await m.reply_text("🎤 Ovozni tinglayapman...", reply_markup=HISOBOT_KLAVIATURASI)
    try:
        voice = m.voice
        if voice is None: return
        ogg_path = Path(tempfile.gettempdir()) / f"voice_{voice.file_id}.ogg"
        tg_file = await context.bot.get_file(voice.file_id)
        await tg_file.download_to_drive(ogg_path)
        text_transcribed = ""
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            try:
                from openai import OpenAI
                client = OpenAI(api_key=openai_key, timeout=60.0)
                with open(ogg_path, "rb") as audio_file:
                    tr = client.audio.transcriptions.create(model="whisper-1", file=audio_file, language="uz")
                    text_transcribed = tr.text
            except Exception as e:
                logger.error(f"OpenAI Whisper xato: {e}")
        if not text_transcribed:
            try:
                import speech_recognition as sr
                from pydub import AudioSegment
                wav_path = ogg_path.with_suffix(".wav")
                audio = AudioSegment.from_ogg(ogg_path)
                audio.export(wav_path, format="wav")
                r = sr.Recognizer()
                with sr.AudioFile(str(wav_path)) as source:
                    audio_data = r.record(source)
                    try:
                        text_transcribed = r.recognize_google(audio_data, language="uz-UZ")
                    except:
                        try:
                            text_transcribed = r.recognize_google(audio_data, language="tr-TR")
                        except:
                            text_transcribed = r.recognize_google(audio_data, language="ru-RU")
                wav_path.unlink(missing_ok=True)
            except Exception as e:
                logger.error(f"SpeechRecognition xato: {e}")
        ogg_path.unlink(missing_ok=True)
        if not text_transcribed or len(text_transcribed.strip()) < 2:
            await m.reply_text("😕 Ovozni tushunmadim, qayta ayta olasizmi? Masalan: 50 ming chiqim bozordan", reply_markup=HISOBOT_KLAVIATURASI)
            return
        await m.reply_text(f"🎧 Tushundim: \"{text_transcribed}\"", reply_markup=HISOBOT_KLAVIATURASI)
        past = text_transcribed.lower()
        summa, turi = summa_top(text_transcribed)
        if summa <= 0 and "hisobot" in past:
            if "bugun" in past: await hisobotni_yubor(update, "bugun")
            elif "kecha" in past: await hisobotni_yubor(update, "kecha")
            elif "hafta" in past: await hisobotni_yubor(update, "hafta")
            elif "oy" in past: await hisobotni_yubor(update, "oy")
            elif "yil" in past: await hisobotni_yubor(update, "yil")
            else: await hisobotni_yubor(update, "jami")
            return
        if summa <= 0:
            await m.reply_text("Summani tushunmadim. Masalan: bugun 50 ming ketdi", reply_markup=HISOBOT_KLAVIATURASI)
            return
        now = datetime.now(TOSHKENT); sana = sanani_top(text_transcribed, now.date()); vaqt = now.strftime("%H:%M")
        try:
            if USE_DB:
                c = get_db_conn(); cur = c.cursor()
                cur.execute("INSERT INTO hisobot (sana,turi,summa,matn,vaqt) VALUES (%s,%s,%s,%s,%s)", (sana,turi,summa,text_transcribed,vaqt))
                c.commit(); cur.close(); c.close()
            else:
                with FAYL.open("a", newline="", encoding="utf-8") as f: csv.writer(f).writerow([sana,turi,summa,text_transcribed,vaqt])
        except Exception:
            logger.exception("Saqlashda xato"); await m.reply_text("Yozuvni saqlashda xatolik.", reply_markup=HISOBOT_KLAVIATURASI); return
        await m.reply_text(f"✅ {sanani_korsatish(sana)} | {turi} {summa:,} so'm saqlandi\n📝 {text_transcribed}", reply_markup=HISOBOT_KLAVIATURASI)
    except Exception as e:
        logger.exception("Voice handler xato")
        await m.reply_text(f"Ovozli xabarda xatolik: {e}", reply_markup=HISOBOT_KLAVIATURASI)

async def xatolikni_qayd_et(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.error is not None: logger.error("Xatolik", exc_info=(type(context.error), context.error, context.error.__traceback__))

def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token: sys.exit("TELEGRAM_BOT_TOKEN topilmadi.")
    csv_faylni_tayyorla()
    ilova = Application.builder().token(token).build()
    ilova.add_handler(CommandHandler("myid", myid))
    ilova.add_handler(CommandHandler("start", start))
    ilova.add_handler(CommandHandler("hisobot", hisobot))
    ilova.add_handler(CallbackQueryHandler(batafsil_hisobot))
    ilova.add_handler(MessageHandler(HISOBOT_TUGMASI_FILTERI, menyu_hisoboti))
    ilova.add_handler(MessageHandler(filters.VOICE, handle_voice))
    # MUHIM: o'chirish handleri yoz dan OLDIN bo'lishi kerak
    ilova.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r"(?i).*(o'|o‘|o’)?chir\.?$"), ochirish_handler))
    ilova.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, yoz))
    ilova.add_error_handler(xatolikni_qayd_et)
    logger.info("Bot ishga tushmoqda - voice + delete enabled.")
    ilova.run_polling()

if __name__ == "__main__": main()
