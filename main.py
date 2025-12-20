import sys, time, requests, json, datetime, os
from web3 import Web3

# ---------------------------------------------------------
# [1] 설정 및 12대 지갑 주소 (v4.6.2 원본 사수)
# ---------------------------------------------------------
TELEGRAM_TOKEN = "8499432639:AAHXj7pZWjNYKFaeBzZTE4iZC-ZYGfsfjjc"
CHAT_ID = "-5074742053"
RPC_URL = "https://bsc-dataseed.binance.org/" 
DATA_FILE = "webkey_daily_data.json"
HISTORY_FILE = "webkey_history.json"

ADDR_LP_POOL = "0x8665a78ccc84d6df2acaa4b207d88c6bc9b70ec5"
ADDR_USDT    = "0x55d398326f99059fF775485246999027B3197955"

TARGETS = [
    ("유동성 LP (시세결정)", ADDR_LP_POOL),
    ("유동성 국고 (현금담보)", "0xbCD506ea39C67f7FD75a12b8a034B9680f7f3F44"),
    ("트레저리 (발행원천)", "0x39c145Ef5Ca969E060802B50a99623909d73e394"),
    ("스테이킹 (자산동결)", "0xa8aCdd81F46633b69AcB6ec5c16Ee7E00cc8938D"),
    ("NFT 부스팅 (홀더보상)", "0x185D5C85486053da0570fDA382c932f83472b261"),
    ("레퍼럴 (추천인보상)", "0xac1ACE3C20d6772436c9Fc79D07b802C03e313CC"),
    ("직급보상풀 (보상적립)", "0x8009F2fcbba15e373253A297CA5f92475a6eb60B"),
    ("직급보상 (보상지급)", "0x14DBdDb81E56Bff3339438261F49D8a5d45f2eF4"),
    ("서비스 매출 (매출입구)", "0x732ecb0a5c4c698797d496005e553b20d7de188c"),
    ("보상 실지급 (최종출구)", "0x81858efa24a5c13f9406cddcce6ebbabf3f6f2a9"),
    ("노드보상배분 (자동배분)", "0x774944ef51742dea0c2bf7276b0269b2e948feff"),
    ("이자배분허브 (복리대기)", "0xffca9396dccb8d6288e770d4e9e211e722f479a4")
]

# [비상 알람 임계치 설정]
ALARM_LIMIT_USDT_OUT = 50000        # 1) 국고 $5만불 유출
ALARM_LIMIT_LP_DROP = 0.10          # 2) LP 내 USDT 10% 급감
ALARM_LIMIT_STAKING_OUT = 100000    # 3) 스테이킹 10만개 이상 해제
HUB_AVG_VOLUME = 500000             # 5) 허브 평소 물량 (2배인 100만개 초과 시 알람)

last_alarm_time = 0

ABI = [{"constant":True,"inputs":[],"name":"token0","outputs":[{"name":"","type":"address"}],"type":"function"},{"constant":True,"inputs":[],"name":"token1","outputs":[{"name":"","type":"address"}],"type":"function"},{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},{"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},{"constant":True,"inputs":[],"name":"getReserves","outputs":[{"name":"_reserve0","type":"uint112"},{"name":"_reserve1","type":"uint112"},{"name":"_blockTimestampLast","type":"uint32"}],"type":"function"},{"constant":True,"inputs":[],"name":"totalSupply","outputs":[{"name":"total","type":"uint256"}],"type":"function"}]

# ---------------------------------------------------------
# [2] 정밀 수사 알람 엔진 (v5.0 신규 탑재)
# ---------------------------------------------------------
def check_emergency_alarms(curr, base):
    global last_alarm_time
    if time.time() - last_alarm_time < 1800: return # 30분 중복 방지
    
    m, bm = curr["META"], base["META"]
    msg = ""
    
    # 1) 국고 $50,000 이상 유출
    u_out = bm['tr_u'] - m['tr_u']
    if u_out > ALARM_LIMIT_USDT_OUT:
        msg += f"🚨 <b>[국고 실탄 유출]</b>\n자정 대비 현금 <b>${u_out:,.0f} (USDT)</b> 증발! 시세 방어전 또는 대량 인출 수사 요망.\n\n"
        
    # 2) LP 내 USDT 10% 이상 급감 (투매 경보)
    lp_u_curr = curr["유동성 LP (시세결정)"]["u"]
    lp_u_base = base["유동성 LP (시세결정)"]["u"]
    if lp_u_base > 0 and (lp_u_base - lp_u_curr) / lp_u_base > ALARM_LIMIT_LP_DROP:
        msg += f"⚠️ <b>[본금 투매 감지]</b>\nLP 내 USDT가 10% 이상 급감! 시세 하방 압력 및 탈출 물량 포착.\n\n"
        
    # 3) 스테이킹 해제 및 경로 추적
    s_w_curr = curr["스테이킹 (자산동결)"]["w"]
    s_w_base = base["스테이킹 (자산동결)"]["w"]
    if s_w_base - s_w_curr > ALARM_LIMIT_STAKING_OUT:
        msg += f"🔍 <b>[고래 이동 포착]</b>\n스테이킹 <b>{s_w_base - s_w_curr:,.0f} WKEY</b> 해제됨! (투매 가능성 vs 지갑 이동 추적 중)\n\n"
        
    # 4) 가용현금 증가율 > 발행량 증가율 (성장 알람)
    u_pct = (m['tr_u'] - bm['tr_u']) / bm['tr_u'] if bm['tr_u'] > 0 else 0
    s_pct = (m['supply'] - bm['supply']) / bm['supply'] if bm['supply'] > 0 else 0
    if u_pct > s_pct and u_pct > 0.01:
        msg += f"📈 <b>[건전성 성장 알람]</b>\n현금 증가율({u_pct*100:.1f}%)이 발행 증가율({s_pct*100:.1f}%) 추월! 담보력 강화 신호.\n\n"
        
    # 5) 이자배분허브 매도 압력 (평소 2배 물량)
    hub_w = curr["이자배분허브 (복리대기)"]["w"]
    if hub_w > HUB_AVG_VOLUME * 2:
        msg += f"📉 <b>[매도 압력 예보]</b>\n이자배분허브에 평소 2배인 <b>{hub_w:,.0f} WKEY</b> 적체! 시장 투하 가능성 대비 요망."

    if msg:
        send_msg(msg)
        last_alarm_time = time.time()

# ... [get_trend_icon, fetch_data, build_report, build_analysis 등 v6.1 로직 그대로 사수] ...

def get_trend_icon(val):
    if val > 0.0001: return "📈"
    if val < -0.0001: return "📉"
    return "➖"

def send_msg(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
    except: pass

def fetch_data(w3):
    lp_con = w3.eth.contract(address=w3.to_checksum_address(ADDR_LP_POOL), abi=ABI)
    is_u0 = lp_con.functions.token0().call().lower() == ADDR_USDT.lower()
    real_wkey = lp_con.functions.token1().call() if is_u0 else lp_con.functions.token0().call()
    w_con = w3.eth.contract(address=real_wkey, abi=ABI)
    u_con = w3.eth.contract(address=ADDR_USDT, abi=ABI)
    res, lp_supply, dec = lp_con.functions.getReserves().call(), lp_con.functions.totalSupply().call(), w_con.functions.decimals().call()
    r_u, r_w = (res[0], res[1]) if is_u0 else (res[1], res[0])
    total_supply = w_con.functions.totalSupply().call() / (10**dec)
    try:
        price_res = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{real_wkey}", timeout=5).json()
        price = float(price_res['pairs'][0].get('priceUsd', 0)) if price_res.get('pairs') else 0.0
    except: price = 0.0
    snap, total_u = {}, 0
    for name, addr in TARGETS:
        t = w3.to_checksum_address(addr)
        raw_w = w_con.functions.balanceOf(t).call() / (10**dec)
        raw_u = u_con.functions.balanceOf(t).call() / 1e18
        lp_bal = lp_con.functions.balanceOf(t).call()
        share = lp_bal / lp_supply if lp_supply > 0 else 0
        f_w, f_u = raw_w + ((r_w * share) / (10**dec)), raw_u + ((r_u * share) / 1e18)
        snap[name] = {"w": f_w, "u": f_u}
        if any(x in name for x in ["국고", "LP"]): total_u += f_u
    ratio = (snap["스테이킹 (자산동결)"]["w"] / total_supply * 100) if total_supply > 0 else 0
    snap["META"] = {"backing": total_u / total_supply if total_supply > 0 else 0, "supply": total_supply, "ratio": ratio, "tr_u": total_u, "price": price}
    return snap

def build_report(curr, base, all_mode=False):
    m, bm = curr["META"], base.get("META", curr["META"])
    pd, pp = m["price"] - bm["price"], ((m["price"] - bm["price"]) / bm["price"] * 100) if bm["price"] > 0 else 0
    ud, up = m["tr_u"] - bm["tr_u"], ((m["tr_u"] - bm["tr_u"]) / bm["tr_u"] * 100) if bm["tr_u"] > 0 else 0
    bd, bp = m["backing"] - bm["backing"], ((m["backing"] - bm["backing"]) / bm["backing"] * 100) if bm["backing"] > 0 else 0
    sd, sp = m["supply"] - bm["supply"], ((m["supply"] - bm["supply"]) / bm["supply"] * 100) if bm["supply"] > 0 else 0
    rd = m["ratio"] - bm["ratio"]
    L = "━━━━━━━━━━━━━━━━━━━━━━━━"
    res = f"<b>🤖 WebKeyDAO 관제 v6.2 (v5.0 통합)</b>\n"
    res += f"💲 시세: <b>${m['price']:.2f}</b> [<b>{pd:+.2f} ({pp:+.2f}%)</b>] {get_trend_icon(pd)}\n"
    res += f"💎 담보: <b>${m['backing']:.3f}</b> (<b>{bp:+.2f}%</b>) {get_trend_icon(bd)}\n"
    res += f"📊 발행: <b>{sd:+,.0f} ({sp:+.2f}%)</b> {get_trend_icon(sd)} | 🔒 락업: <b>{m['ratio']:.1f}% ({rd:+.2f}%p)</b> {get_trend_icon(rd)}\n"
    res += f"📉 기준: 자정(00:00) 대비 증감\n{L}\n"
    for n, _ in TARGETS:
        if not all_mode and n not in ["유동성 LP (시세결정)", "유동성 국고 (현금담보)", "트레저리 (발행원천)", "스테이킹 (자산동결)"]: continue
        c, b = curr[n], base.get(n, curr[n])
        wd, wp = c['w'] - b['w'], ((c['w'] - b['w']) / b['w'] * 100) if b['w'] > 0 else 0
        res += f"📌 <b>{n}</b>\n • WKEY: {c['w']:,.0f} [<b>{wd:+,.0f} ({wp:+.1f}%)</b>] {get_trend_icon(wd)}\n"
        if c['u'] > 1:
            uds, ups = c['u'] - b['u'], ((c['u'] - b['u']) / b['u'] * 100) if b['u'] > 0 else 0
            res += f" • USDT: <b>${c['u']:,.0f}</b> [<b>${uds:+,.0f} ({ups:+.1f}%)</b>] {get_trend_icon(uds)}\n"
        res += f"{L}\n"
    return res + f"💰 <b>총 가용현금: ${m['tr_u']:,.0f}</b> [<b>{ud:+,.0f} ({up:+.2f}%)</b>] {get_trend_icon(ud)}"

def build_analysis(days, curr):
    if not os.path.exists(HISTORY_FILE): return "⚠️ 데이터 누적 중... 내일부터 분석 가능합니다."
    with open(HISTORY_FILE, 'r') as f: history = json.load(f)
    if not history: return "⚠️ 데이터 부족"
    idx = min(len(history), days)
    past = history[-idx]
    p_meta, c_meta = past["data"]["META"], curr["META"]
    p_diff, p_pct = c_meta['price'] - p_meta['price'], ((c_meta['price'] - p_meta['price']) / p_meta['price'] * 100) if p_meta['price'] > 0 else 0
    u_diff, u_pct = c_meta['tr_u'] - p_meta['tr_u'], ((c_meta['tr_u'] - p_meta['tr_u']) / p_meta['tr_u'] * 100) if p_meta['tr_u'] > 0 else 0
    L = "━━━━━━━━━━━━━━━━━━━━━━━━"
    res = f"<b>📊 {days}일 추세 보고</b>\n기준일: {past['date']}\n{L}\n"
    res += f"📉 시세: <b>{p_diff:+.2f} ({p_pct:+.2f}%)</b>\n💰 가용현금: <b>${u_diff:+,.0f} ({u_pct:+.2f}%)</b>\n{L}"
    return res

# ---------------------------------------------------------
# [4] 메인 루프 (명령어 및 비상 알람 연동)
# ---------------------------------------------------------
if __name__ == "__main__":
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if w3.is_connected():
        send_msg("🚀 <b>관제 v6.2 (v5.0 통합본) 엔진 기동</b>\n(5대 수사 알람 및 외형 복구 완료)")
        last_rep, off = time.time() - 590, 0
        while True:
            try:
                curr_data = fetch_data(w3)
                today = str(datetime.date.today())
                if not os.path.exists(DATA_FILE):
                    with open(DATA_FILE, 'w') as f: json.dump({"date": today, "data": curr_data}, f)
                    base_data = curr_data
                else:
                    with open(DATA_FILE, 'r') as f: saved = json.load(f)
                    if saved.get("date") != today:
                        h_data = []
                        if os.path.exists(HISTORY_FILE):
                            with open(HISTORY_FILE, 'r') as f: h_data = json.load(f)
                        h_data.append(saved)
                        if len(h_data) > 60: h_data.pop(0)
                        with open(HISTORY_FILE, 'w') as f: json.dump(h_data, f)
                        with open(DATA_FILE, 'w') as f: json.dump({"date": today, "data": curr_data}, f)
                        base_data = curr_data
                    else: base_data = saved["data"]

                # 1. 비상 알람 체크 (v5.0 핵심 기능)
                check_emergency_alarms(curr_data, base_data)

                # 2. 자동 보고 (10분 주기)
                if time.time() - last_rep > 600:
                    send_msg(build_report(curr_data, base_data, False))
                    last_rep = time.time()

                # 3. 명령어 처리
                up_res = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates", params={"offset": off, "timeout": 2}).json()
                for up in up_res.get("result", []):
                    off = up["update_id"] + 1
                    msg = up.get("message", {}).get("text", "").lower().strip()
                    if any(x in msg for x in ["보고서", "/보고서", "all", "/all"]):
                        send_msg(build_report(curr_data, base_data, "all" in msg))
                    elif "주간" in msg: send_msg(build_analysis(7, curr_data))
                
                time.sleep(5)
            except Exception as e: time.sleep(10)
