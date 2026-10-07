"""Telegram bot for recording income and expenses in a local CSV file."""

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


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
# HTTPX request logs include the full Telegram URL, which contains the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

FAYL = Path(__file__).resolve().with_name("hisobot.csv")
ESKI_USTUNLAR = ["sana", "turi", "summa", "matn"]
USTUNLAR = [*ESKI_USTUNLAR, "vaqt"]
TOSHKENT = ZoneInfo("Asia/Tashkent")

HISOBOT_TUGMALARI = {
    "📅 Bugungi hisobot": "bugun",
    "📆 Kechagi hisobot": "kecha",
    "📊 Haftalik hisobot": "hafta",
    "🗓 Oylik hisobot": "oy",
    "📈 Yillik hisobot": "yil",
}
DAVR_CALLBACK_TOKENLARI = {
    "bugun": "1d",
    "kecha": "yesterday",
    "hafta": "7d",
    "oy": "month",
    "yil": "year",
}
CALLBACK_TOKEN_DAVRLARI = {
    token: davr for davr, token in DAVR_CALLBACK_TOKENLARI.items()
}
CALLBACK_TURI_NOMLARI = {
    "expense": "CHIQIM",
    "income": "KIRIM",
}
DAVR_BATAFSIL_SARLAVHALARI = {
    "bugun": "Kunlik",
    "kecha": "Kechagi",
    "hafta": "Haftalik",
    "oy": "Oylik",
    "yil": "Yillik",
}
ESKI_BATAFSIL_CALLBACKLAR = {
    "detail_bugungi": ("bugun", "CHIQIM"),
    "detail_kechagi": ("kecha", "CHIQIM"),
    "detail_haftalik": ("hafta", "CHIQIM"),
    "detail_oylik": ("oy", "CHIQIM"),
    "detail_yillik": ("yil", "CHIQIM"),
}
HISOBOT_KLAVIATURASI = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton("📅 Bugungi hisobot"),
            KeyboardButton("📆 Kechagi hisobot"),
        ],
        [
            KeyboardButton("📊 Haftalik hisobot"),
            KeyboardButton("🗓 Oylik hisobot"),
        ],
        [KeyboardButton("📈 Yillik hisobot")],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
    is_persistent=True,
)
HISOBOT_TUGMASI_FILTERI = filters.Regex(
    r"^(?:" + "|".join(re.escape(tugma) for tugma in HISOBOT_TUGMALARI) + r")$"
)


def sanani_top(matn: str, bugun: date | None = None) -> str:
    """Find a relative date in a message, defaulting to today in Tashkent."""
    matn = matn.lower()
    bugun = bugun or datetime.now(TOSHKENT).date()

    if re.search(r"\bkecha\b", matn):
        return (bugun - timedelta(days=1)).isoformat()

    kunlar = re.search(r"\b(\d+)\s*kun\s+oldin\b", matn)
    if kunlar:
        return (bugun - timedelta(days=int(kunlar.group(1)))).isoformat()

    return bugun.isoformat()


def sanani_korsatish(sana: date | str) -> str:
    """Format a date for user-facing messages as day.month.year."""
    if isinstance(sana, str):
        sana = date.fromisoformat(sana)
    return sana.strftime("%d.%m.%Y")


def summa_top(matn: str) -> tuple[int, str]:
    """Extract an amount and classify the message as income or expense."""
    kichik = (
        matn.lower()
        .replace("’", "'")
        .replace("‘", "'")
        .replace("ʻ", "'")
        .replace("ʼ", "'")
    )

    million = re.search(r"(\d+(?:[.,]\d+)?)\s*mln\b", kichik)
    ming = re.search(r"(\d+)\s*ming\b", kichik)
    oddiy = re.search(r"\b(\d{4,})\b", kichik)

    if million:
        summa = int(Decimal(million.group(1).replace(",", ".")) * 1_000_000)
    elif ming:
        summa = int(ming.group(1)) * 1_000
    elif oddiy:
        summa = int(oddiy.group(1))
    else:
        summa = 0

    turi = "KIRIM" if re.search(r"\bkrim\b", kichik) else "CHIQIM"

    return summa, turi


def csv_faylni_tayyorla() -> None:
    """Create the ledger with its header if it does not exist yet."""
    if not FAYL.exists() or FAYL.stat().st_size == 0:
        with FAYL.open("w", newline="", encoding="utf-8") as fayl:
            csv.writer(fayl).writerow(USTUNLAR)
        return

    with FAYL.open("r", newline="", encoding="utf-8") as fayl:
        sarlavha = next(csv.reader(fayl), None)
    if sarlavha != ESKI_USTUNLAR:
        return

    vaqtinchalik_fayl = None
    try:
        with FAYL.open("r", newline="", encoding="utf-8") as eski_fayl:
            yozuvlar = csv.DictReader(eski_fayl)
            with tempfile.NamedTemporaryFile(
                mode="w",
                newline="",
                encoding="utf-8",
                dir=FAYL.parent,
                prefix=f".{FAYL.name}.",
                suffix=".tmp",
                delete=False,
            ) as yangi_fayl:
                vaqtinchalik_fayl = Path(yangi_fayl.name)
                yozuvchi = csv.writer(yangi_fayl)
                yozuvchi.writerow(USTUNLAR)
                for qator in yozuvlar:
                    yozuvchi.writerow(
                        [
                            qator.get("sana", ""),
                            qator.get("turi", ""),
                            qator.get("summa", ""),
                            qator.get("matn", ""),
                            "",
                        ]
                    )
        os.replace(vaqtinchalik_fayl, FAYL)
    except BaseException:
        if vaqtinchalik_fayl is not None:
            vaqtinchalik_fayl.unlink(missing_ok=True)
        raise


def egasimi(update: Update) -> bool:
    """Allow ledger actions only for the configured Telegram account."""
    user = update.effective_user
    chat = update.effective_chat
    owner_id = os.getenv("TELEGRAM_OWNER_ID", "").strip()
    if (
        user is None
        or chat is None
        or chat.type != ChatType.PRIVATE
        or not owner_id
    ):
        return False

    try:
        return user.id == int(owner_id)
    except ValueError:
        return False


async def shaxsiyligini_ayt(update: Update) -> None:
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            "Bu bot shaxsiy",
            reply_markup=ReplyKeyboardRemove(),
        )


async def myid(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None:
        return

    if chat is not None and chat.type != ChatType.PRIVATE:
        await message.reply_text(
            "ID raqamingizni olish uchun botga shaxsiy xabar yuboring."
        )
        return

    await message.reply_text(
        f"Telegram ID raqamingiz: {user.id}",
        reply_markup=(
            HISOBOT_KLAVIATURASI if egasimi(update) else ReplyKeyboardRemove()
        ),
    )


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    if message is None:
        return
    if not egasimi(update):
        await shaxsiyligini_ayt(update)
        return

    await message.reply_text(
        "Kirim-chiqim botiga xush kelibsiz!\n"
        "Masalan: bugun 50 ming tushlik qildim\n"
        "Yoki: kecha krim 2 mln maosh keldi\n"
        "/hisobot — jami hisobotni ko'rish\n"
        "Hisobot turlari uchun pastdagi doimiy menyudan foydalaning.",
        reply_markup=HISOBOT_KLAVIATURASI,
    )


async def yoz(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    if message is None or message.text is None:
        return
    if not egasimi(update):
        await shaxsiyligini_ayt(update)
        return

    summa, turi = summa_top(message.text)
    if summa <= 0:
        await message.reply_text(
            "Summani tushunmadim. Masalan: bugun 50 ming ketdi",
            reply_markup=HISOBOT_KLAVIATURASI,
        )
        return

    now = datetime.now(TOSHKENT)
    sana = sanani_top(message.text, now.date())
    vaqt = now.strftime("%H:%M")
    try:
        with FAYL.open("a", newline="", encoding="utf-8") as fayl:
            csv.writer(fayl).writerow([sana, turi, summa, message.text, vaqt])
    except OSError:
        logger.exception("Moliyaviy yozuvni CSV faylga saqlab bo'lmadi.")
        await message.reply_text(
            "Yozuvni saqlashda xatolik yuz berdi.",
            reply_markup=HISOBOT_KLAVIATURASI,
        )
        return

    await message.reply_text(
        f"✅ {sanani_korsatish(sana)} | {turi} {summa:,} so'm saqlandi",
        reply_markup=HISOBOT_KLAVIATURASI,
    )


def hisobot_oraligini_top(davr: str) -> tuple[date | None, date | None, str]:
    """Return inclusive report boundaries and their Uzbek title."""
    bugun = datetime.now(TOSHKENT).date()

    if davr == "bugun":
        return bugun, bugun, f"Bugungi hisobot ({sanani_korsatish(bugun)})"
    if davr == "kecha":
        kecha = bugun - timedelta(days=1)
        return kecha, kecha, f"Kechagi hisobot ({sanani_korsatish(kecha)})"
    if davr == "hafta":
        boshlanish = bugun - timedelta(days=6)
        return boshlanish, bugun, (
            f"Haftalik hisobot ({sanani_korsatish(boshlanish)} — "
            f"{sanani_korsatish(bugun)})"
        )
    if davr == "oy":
        boshlanish = bugun.replace(day=1)
        return boshlanish, bugun, (
            f"Oylik hisobot ({sanani_korsatish(boshlanish)} — "
            f"{sanani_korsatish(bugun)})"
        )
    if davr == "yil":
        boshlanish = bugun.replace(month=1, day=1)
        return boshlanish, bugun, (
            f"Yillik hisobot ({sanani_korsatish(boshlanish)} — "
            f"{sanani_korsatish(bugun)})"
        )
    if davr == "jami":
        return None, None, "Umumiy hisobot"

    raise ValueError(f"Noma'lum hisobot davri: {davr}")


def davr_yozuvlarini_oqi(
    boshlanish: date | None,
    tugash: date | None,
) -> list[dict[str, object]]:
    """Read valid transactions within an inclusive date range."""
    with FAYL.open("r", newline="", encoding="utf-8") as fayl:
        qatorlar = csv.DictReader(fayl)

        tanlangan = []
        for qator in qatorlar:
            try:
                sana = date.fromisoformat(qator["sana"])
                summa = int(qator["summa"])
                turi = qator["turi"]
            except (KeyError, TypeError, ValueError):
                logger.warning("Noto'g'ri CSV qatori hisobotdan o'tkazib yuborildi.")
                continue

            if turi not in ("KIRIM", "CHIQIM"):
                continue
            if boshlanish is not None and sana < boshlanish:
                continue
            if tugash is not None and sana > tugash:
                continue

            tanlangan.append(
                {
                    "sana": sana,
                    "vaqt": (qator.get("vaqt") or "").strip(),
                    "turi": turi,
                    "summa": summa,
                    "matn": qator.get("matn") or "",
                }
            )

    return tanlangan


def hisobot_inline_klaviaturasi(davr: str) -> InlineKeyboardMarkup | None:
    """Create expense and income detail buttons for a period report."""
    davr_tokeni = DAVR_CALLBACK_TOKENLARI.get(davr)
    if davr_tokeni is None:
        return None

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📋 Nimalarga sarfladim?",
                    callback_data=f"report-detail-expense:{davr_tokeni}",
                ),
                InlineKeyboardButton(
                    "💰 Kirimlar",
                    callback_data=f"report-detail-income:{davr_tokeni}",
                )
            ]
        ]
    )


async def hisobotni_yubor(
    update: Update,
    davr: str,
) -> None:
    message = update.effective_message
    if message is None:
        return
    if not egasimi(update):
        await shaxsiyligini_ayt(update)
        return

    boshlanish, tugash, sarlavha = hisobot_oraligini_top(davr)
    try:
        yozuvlar = davr_yozuvlarini_oqi(boshlanish, tugash)
        kirim = sum(
            qator["summa"] for qator in yozuvlar if qator["turi"] == "KIRIM"
        )
        chiqim = sum(
            qator["summa"] for qator in yozuvlar if qator["turi"] == "CHIQIM"
        )
    except (OSError, csv.Error, KeyError, TypeError, ValueError):
        logger.exception("Hisobot CSV faylidan o'qilmadi.")
        await message.reply_text(
            "Hisobotni o'qishda xatolik yuz berdi.",
            reply_markup=HISOBOT_KLAVIATURASI,
        )
        return

    inline_klaviatura = hisobot_inline_klaviaturasi(davr)
    await message.reply_text(
        f"{sarlavha}\n"
        f"Kirim: {kirim:,} so'm\n"
        f"Chiqim: {chiqim:,} so'm\n"
        f"Qoldiq: {kirim - chiqim:,} so'm",
        reply_markup=inline_klaviatura or HISOBOT_KLAVIATURASI,
    )


def tranzaksiya_izohi(matn: str) -> str:
    """Remove date, amount, and action words to leave the entered note."""
    izoh = re.sub(
        r"\b\d+\s*kun\s+oldin\b|\b(?:bugun|kecha)\b",
        " ",
        matn,
        flags=re.IGNORECASE,
    )
    izoh = re.sub(
        r"\b\d+(?:[.,]\d+)?\s*(?:mln|ming)\b|\b\d{4,}\b",
        " ",
        izoh,
        flags=re.IGNORECASE,
    )
    izoh = re.sub(
        r"\b(?:ketdi|sarfl\w*|to[’‘ʻʼ']ladim|oldim|qildim|"
        r"topdim|keldi|tushdi)\b",
        " ",
        izoh,
        flags=re.IGNORECASE,
    )
    return " ".join(izoh.split()).strip(" -–—") or "Izoh yo‘q"


def batafsil_hisobot_matni(davr: str, turi: str) -> str:
    """Format one transaction category for a period with local timestamps."""
    boshlanish, tugash, _ = hisobot_oraligini_top(davr)
    if boshlanish is None or tugash is None:
        raise ValueError("Batafsil hisobot uchun davr tanlanishi kerak.")

    if turi not in ("KIRIM", "CHIQIM"):
        raise ValueError(f"Noma'lum tranzaksiya turi: {turi}")

    yozuvlar = [
        qator
        for qator in davr_yozuvlarini_oqi(boshlanish, tugash)
        if qator["turi"] == turi
    ]
    yozuvlar.sort(key=lambda qator: (qator["sana"], qator["vaqt"] or "00:00"))

    if not yozuvlar:
        tur_nomi = "kirim" if turi == "KIRIM" else "chiqim"
        return f"Bu davrda {tur_nomi} bo'lmagan"

    tur_nomi = "kirimlar" if turi == "KIRIM" else "chiqimlar"
    sarlavha = f"{DAVR_BATAFSIL_SARLAVHALARI[davr]} {tur_nomi}:"
    satrlar = [sarlavha]

    for qator in yozuvlar:
        vaqt = qator["vaqt"]
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", vaqt):
            vaqt = "--:--"

        kirimmi = turi == "KIRIM"
        belgi = "💰" if kirimmi else "💸"
        ishora = "+" if kirimmi else "-"
        izoh = tranzaksiya_izohi(qator["matn"])
        satrlar.append(
            f"{belgi} {sanani_korsatish(qator['sana'])} {vaqt} "
            f"{ishora}{abs(qator['summa']):,} so'm - {izoh}"
        )

    return "\n".join(satrlar)


def matnni_telegramga_bol(matn: str, chegara: int = 3800) -> list[str]:
    """Split a long report into Telegram-safe messages without dropping lines."""
    qismlar = []
    joriy = ""
    for satr in matn.splitlines():
        if len(satr) > chegara:
            if joriy:
                qismlar.append(joriy)
                joriy = ""
            for index in range(0, len(satr), chegara):
                qismlar.append(satr[index : index + chegara])
            continue
        yangi_uzunlik = len(joriy) + len(satr) + (1 if joriy else 0)
        if joriy and yangi_uzunlik > chegara:
            qismlar.append(joriy)
            joriy = satr
        else:
            joriy = f"{joriy}\n{satr}" if joriy else satr
    if joriy:
        qismlar.append(joriy)
    return qismlar


async def batafsil_hisobot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Serve the requested detail callback only to the configured owner."""
    query = update.callback_query
    if query is None:
        return
    if not egasimi(update):
        await query.answer("Bu bot shaxsiy", show_alert=True)
        await shaxsiyligini_ayt(update)
        return

    callback_data = query.data or ""
    if callback_data in ESKI_BATAFSIL_CALLBACKLAR:
        davr, turi = ESKI_BATAFSIL_CALLBACKLAR[callback_data]
    else:
        prefix, separator, davr_tokeni = callback_data.partition(":")
        kategoriya = prefix.removeprefix("report-detail-")
        davr = CALLBACK_TOKEN_DAVRLARI.get(davr_tokeni) if separator else None
        turi = CALLBACK_TURI_NOMLARI.get(kategoriya)
    if davr is None or turi is None:
        await query.answer("Hisobot davri topilmadi.", show_alert=True)
        return

    await query.answer()
    message = update.effective_message
    if message is None:
        return
    try:
        qismlar = matnni_telegramga_bol(batafsil_hisobot_matni(davr, turi))
    except (OSError, csv.Error, KeyError, TypeError, ValueError):
        logger.exception("Batafsil hisobot CSV faylidan o'qilmadi.")
        await message.reply_text(
            "Batafsil hisobotni o'qishda xatolik yuz berdi.",
            reply_markup=HISOBOT_KLAVIATURASI,
        )
        return

    for indeks, qism in enumerate(qismlar):
        await message.reply_text(
            qism,
            reply_markup=HISOBOT_KLAVIATURASI if indeks == 0 else None,
        )


async def hisobot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle /hisobot, optionally for a named period."""
    if not egasimi(update):
        await shaxsiyligini_ayt(update)
        return

    args = context.args
    if not args:
        await hisobotni_yubor(update, "jami")
        return

    davr = {
        "bugun": "bugun",
        "kecha": "kecha",
        "haftalik": "hafta",
        "oylik": "oy",
        "yillik": "yil",
    }.get(args[0].lower())
    if davr is None or len(args) > 1:
        message = update.effective_message
        if message is not None:
            await message.reply_text(
                "Foydalanish: /hisobot [bugun|kecha|haftalik|oylik|yillik]",
                reply_markup=(
                    HISOBOT_KLAVIATURASI
                    if egasimi(update)
                    else ReplyKeyboardRemove()
                ),
            )
        return

    await hisobotni_yubor(update, davr)


async def menyu_hisoboti(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Route persistent keyboard buttons through the shared report logic."""
    message = update.effective_message
    if message is None or message.text is None:
        return
    davr = HISOBOT_TUGMALARI.get(message.text)
    if davr is not None:
        await hisobotni_yubor(update, davr)


async def xatolikni_qayd_et(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    error = context.error
    if error is not None:
        logger.error(
            "Yangilanishni qayta ishlashda xatolik yuz berdi.",
            exc_info=(type(error), error, error.__traceback__),
        )


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit(
            "TELEGRAM_BOT_TOKEN topilmadi. Uni Replit Secrets bo'limiga "
            "qo'shing."
        )

    owner_id = os.getenv("TELEGRAM_OWNER_ID", "").strip()
    if owner_id:
        try:
            if int(owner_id) <= 0:
                raise ValueError
        except ValueError:
            sys.exit("TELEGRAM_OWNER_ID musbat son bo'lishi kerak.")
    else:
        logger.warning(
            "TELEGRAM_OWNER_ID sozlanmagan; /myid dan boshqa buyruqlar "
            "hozircha yopiq."
        )

    csv_faylni_tayyorla()
    ilova = Application.builder().token(token).build()
    ilova.add_handler(CommandHandler("myid", myid))
    ilova.add_handler(CommandHandler("start", start))
    ilova.add_handler(CommandHandler("hisobot", hisobot))
    ilova.add_handler(CallbackQueryHandler(batafsil_hisobot))
    ilova.add_handler(
        MessageHandler(HISOBOT_TUGMASI_FILTERI, menyu_hisoboti)
    )
    ilova.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, yoz)
    )
    ilova.add_error_handler(xatolikni_qayd_et)

    logger.info("Telegram bot ishga tushmoqda.")
    ilova.run_polling()


if __name__ == "__main__":
    main()
