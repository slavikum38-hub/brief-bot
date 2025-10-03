import os, time, math, datetime, requests, re
from dotenv import load_dotenv

# ====== НАСТРОЙКИ ======
# Фиксированные уровни Фибо (как ты просил)
FIB_LEVELS = {
   "pyth": [("1.618", 1.8095), ("2.618", 2.8712), ("3.618", 3.9329)],
   "ada":  [("1.618", 4.8788), ("2.618", 7.7801), ("3.618",10.6815)],
   "fet":  [("1.618", 5.3960), ("2.618", 8.5070), ("3.618",11.6180)],
   "doge": [("1.618", 1.1597), ("2.618", 1.8442), ("3.618", 2.5287)],
   "arb":  [("1.618", 3.6833), ("2.618", 5.7914), ("3.618", 7.8996)],
   "wlfi": []  # уровни добавим позже, когда появится стабильная референс-цена
}

# Соответствие тикеров CoinGecko
COINGECKO_IDS = {
   "btc":"bitcoin",
   "eth":"ethereum",
   "usdt":"tether",
   "pyth":"pyth-network",
   "ada":"cardano",
   "fet":"fetch-ai",
   "doge":"dogecoin",
   "arb":"arbitrum",
   "wlfi":"wlfi"  # если токена нет на CG — вернём N/A
}

# ====== СЕКРЕТЫ ======
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

def send_msg(text: str):
   r = requests.post(
       f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
       data={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
       timeout=30
   )
   return r.status_code, r.text

def pct_emoji(p):
   return "🟢" if (p is not None and p >= 0) else "🔴"

def nice_usd(x, digits=2):
   return f"${x:,.{digits}f}"

# ====== COINGECKO ======
def cg_simple_prices(ids, vs="usd"):
   url = "https://api.coingecko.com/api/v3/simple/price"
   params = {"ids": ",".join(ids), "vs_currencies": vs, "include_24hr_change": "true"}
   r = requests.get(url, params=params, timeout=40)
   return r.json() if r.status_code == 200 else {}

def cg_global():
   r = requests.get("https://api.coingecko.com/api/v3/global", timeout=40)
   return r.json().get("data", {}) if r.status_code == 200 else {}

def cg_market_cap(id_):
   url = f"https://api.coingecko.com/api/v3/coins/{id_}"
   r = requests.get(url, params={
       "localization":"false","tickers":"false","market_data":"true",
       "community_data":"false","developer_data":"false","sparkline":"false"
   }, timeout=40)
   if r.status_code == 200:
       md = r.json().get("market_data",{})
       return md.get("market_cap",{}).get("usd")
   return None

def cg_last_daily_ohlc(id_):
   """Последняя дневная свеча (за 2 дня), формат [t, o, h, l, c]."""
   url = f"https://api.coingecko.com/api/v3/coins/{id_}/ohlc"
   r = requests.get(url, params={"vs_currency":"usd","days":"2"}, timeout=40)
   if r.status_code == 200 and isinstance(r.json(), list) and r.json():
       return r.json()[-1]
   return None

def has_upper_wick(ohlc):
   """Эвристика «хвост сверху»: верхняя тень >> тела и >1% от цены."""
   try:
       _, o, h, l, c = ohlc
       body = abs(c - o)
       upper = h - max(o, c)
       if upper > 0.015 * c and (body == 0 or upper > 1.5 * body):
           return True
       return False
   except:
       return False

# ====== МЕТРИКИ/НОВОСТИ ======
def fear_greed():
   try:
       r = requests.get("https://api.alternative.me/fng/?limit=1&format=json", timeout=20)
       if r.status_code == 200:
           v = r.json()["data"][0]
           return int(v["value"])
   except:
       pass
   return None

def fng_grade(v):
   # 🟢 до 40 / 🟡 40–65 / 🔴 >65
   if v is None: return "⚪️"
   if v > 65: return "🔴"
   if v >= 40: return "🟡"
   return "🟢"

def headlines():
   try:
       rss = requests.get("https://www.coindesk.com/arc/outboundfeeds/rss/", timeout=20).text
       titles = re.findall(r"<title>([^<]+)</title>", rss)
       items = [t for t in titles[1:4] if t and "CoinDesk" not in t][:3]
       return items
   except:
       return []

# ====== БРИФ ======
def build_brief():
   # 1) Цены
   ids = [COINGECKO_IDS[x] for x in ["btc","eth","pyth","ada","fet","doge","arb","wlfi"] if COINGECKO_IDS.get(x)]
   prices = cg_simple_prices(ids)

   def get_price_change(cg_id):
       d = prices.get(cg_id, {})
       return d.get("usd"), d.get("usd_24h_change")

   btc_p, btc_ch = get_price_change(COINGECKO_IDS["btc"])
   eth_p, eth_ch = get_price_change(COINGECKO_IDS["eth"])

   # 2) Доминации и TOTAL2
   g = cg_global()
   total_mcap = (g.get("total_market_cap") or {}).get("usd")
   btc_mcap   = cg_market_cap(COINGECKO_IDS["btc"])
   usdt_mcap  = cg_market_cap(COINGECKO_IDS["usdt"])

   usdt_dom = round(100*usdt_mcap/total_mcap, 2) if usdt_mcap and total_mcap else None
   total2   = (total_mcap - btc_mcap) if total_mcap and btc_mcap else None

   # 3) Метрики
   fng = fear_greed()
   fng_g = fng_grade(fng)

   # 4) Портфель
   def coin_block(tk, label):
       cgid = COINGECKO_IDS.get(tk)
       price, chg = (None, None)
       if cgid:
           price, chg = get_price_change(cgid)
       price_s = nice_usd(price, 4) if isinstance(price,(int,float)) else "N/A"
       chg_s = f"{chg:+.2f}%" if isinstance(chg,(int,float)) else "N/A"
       em = pct_emoji(chg if isinstance(chg,(int,float)) else 0)

       fibs = FIB_LEVELS.get(tk, [])
       if fibs:
           fib_text = " | ".join([f"{lvl} → {nice_usd(val,4)}" for lvl,val in fibs])
           fib_line = "\n" + "   " + fib_text
       else:
           fib_line = "\n   (уровни добавим позже)"

       alert = ""
       if cgid:
           ohlc = cg_last_daily_ohlc(cgid)
           if ohlc and has_upper_wick(ohlc):
               alert = "\n   ⚠️ *Есть хвост сверху на дневке* — будь внимателен к фиксации."

       return f"• {label}: {price_s}  {chg_s} {em}" + fib_line + alert

   portfolio = "\n".join([
       coin_block("pyth","PYTH"),
       coin_block("ada","ADA"),
       coin_block("fet","FET"),
       coin_block("doge","DOGE"),
       coin_block("arb","ARB"),
       coin_block("wlfi","WLFI"),
   ])

   # 5) Новости
   news = headlines()
   news_block = "\n".join([f"• {t}" for t in news]) if news else "Важных новостей нет."

   # 6) Финальный текст
   header = "🧠 *ЕЖЕДНЕВНЫЙ БРИФ*\n_(актуально на момент отправки)_\n"

   s1 = []
   def line_price(name, p, ch):
       p_s = nice_usd(p) if isinstance(p,(int,float)) else "N/A"
       ch_s = f"{ch:+.2f}%" if isinstance(ch,(int,float)) else "N/A"
       return f"• {name}: {p_s}  {ch_s} {pct_emoji(ch if isinstance(ch,(int,float)) else 0)}"

   s1.append("*1) 📈 Состояние рынка*")
   s1.append(line_price("BTC", btc_p, btc_ch))
   s1.append(line_price("ETH", eth_p, eth_ch))
   s1.append(f"• USDT.D: {usdt_dom:.2f}%  {pct_emoji(0)}" if usdt_dom is not None else "• USDT.D: N/A")
   s1.append(f"• TOTAL2: {nice_usd(total2/1e12,3)} T  {pct_emoji(0)}" if isinstance(total2,(int,float)) else "• TOTAL2: N/A")

   s2 = []
   s2.append("*2) 🧮 Метрики рынка*")
   s2.append(f"• Fear & Greed: {fng if fng is not None else 'N/A'} ({fng_g})")
   s2.append("• NUPL: N/A (без Glassnode) ⚪️")
   s2.append("• MVRV: N/A (без Glassnode) ⚪️")
   s2.append("💡 *Комментарий:* при росте F&G к 75–80 — повышаем настороженность; без NUPL/MVRV ориентируемся на цену, хвосты и USDT.D.")

   s3 = []
   s3.append("*3) 🌕 Портфель — цены и уровни*")
   s3.append(portfolio)

   s4 = []
   s4.append("*4) 🗞 Новости за сутки*")
   s4.append(news_block)
   s4.append("💬 *На что смотреть:* регуляторные новости (SEC/ETF), крупные листинги и хардфорки — это мгновенные триггеры волатильности.")

   s5 = []
   s5.append("*5) 📉 Сигналы риска/фиксации*")
   s5.append("• Длинные тени сверху на дневке + рывок > 8–10% за сутки → частичная фиксация.")
   s5.append("• Рост USDT.D вместе с просадкой TOTAL2 → риск разворота/шорт-сквиза.")
   s5.append("• При F&G → 🔴 — фиксируем ступенчато.")

   text = header + "\n".join(["\n".join(s1), "\n".join(s2), "\n".join(s3), "\n".join(s4), "\n".join(s5)])
   return text

def main():
   txt = build_brief()
   code, resp = send_msg(txt)
   print("Telegram:", code, resp)

if __name__ == "__main__":
   main()
