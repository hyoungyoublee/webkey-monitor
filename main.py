import sys, time, requests, json, datetime, os
from web3 import Web3

# ---------------------------------------------------------
# [1] 설정 및 환경 변수
# ---------------------------------------------------------
RUN_FROM = "PC"  # 리플릿 실행 시 "Replit"으로 수정

TELEGRAM_TOKEN = "8499432639:AAFp7aLo3Woum2FeAA23kJTKFDMCZ0rMqM8"
CHAT_ID = "-5074742053"
RPC_URL = "https://bsc-dataseed.binance.org/" #

GITHUB_BASE = "https://raw.githubusercontent.com/hyoungyoublee/webkey-monitor/refs/heads/main/"
DAILY_FILE = "webkey_daily_data.json"
WEEKLY_FILE = "webkey_weekly_data.json"
MONTHLY_FILE = "webkey_monthly_data.json"

ADDR_LP_POOL = "0x8665a78ccc84d6df2acaa4b207d88c6bc9b70ec5"
ADDR_USDT    = "0x55d398326f99059fF775485246999027B3197955"

TARGETS = [
    ("유동성 LP (시세결정)", ADDR_LP_POOL),
    ("유동성 국고 (현금담보)", "0xbCD506ea39C67f7FD75a12b8a034B9680f7f3F44"),
    ("트레저리 (발행원천)", "0x39c145Ef5Ca969E060802B50a99623909d73e394"),
    ("스테이킹 (자산동결)", "0xa8aCdd81F46633b69AcB6ec5c16Ee7E00cc8938D"),
    ("NFT 부스팅 (홀더보상)", "0x185D5C85486053da0570FDA382c932f83472b261"),
    ("레퍼럴 (추천인보상)", "0xac1ACE3C20d6772436c9Fc79D07B802C03E313CC"),
    ("직급보상풀 (보상적립)", "0x8009F2fcbba15e373253A297CA5f92475a6eb60B"),
    ("직급보상 (보상지급)", "0x14DBdDb81E56Bff3339438261F49D8a5d45f2ef4"),
    ("서비스 매출 (매출입구)", "0x732ecb0a5c4c698797d496005e553b20d7de188c"),
    ("보상 실지급 (최종출구)", "0x81858efa24a5c13f9406cdddce6ebbabf3f6f2a9"),
    ("노드보상배분 (자동배분)", "0x774944ef51742dea0c2bf7276b0269b2e948feff"),
    ("이자배분허브 (복리대기)", "0xffca9396dccb8d6288e770d4e9e211e722f479a4")
]

ALARM_LIMIT_USDT_OUT = 50000 
alert_history = [] 
ABI = [{"constant":True,"inputs":[],"name":"token0","outputs":[{"name":"","type":"address"}],"type":"function"},{"constant":True,"inputs":[],"name":"token1","outputs":[{"name":"","type":"address"}],"type":"function"},{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},{"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},{"constant":True,"inputs":[],"name":"getReserves","outputs":[{"name":"_reserve0","type":"uint112"},{"name":"_reserve1","type":"uint112"},{"name":"_blockTimestampLast","type":"uint32"}],"type":"function"},{"constant":True,"inputs":[],"name":"totalSupply","outputs":[{"name":"total","type":"uint256"}],"type":"function"}]

# ---------------------------------------------------------
# [2] 핵심 함수
# ---------------------------------------------------------

def send_msg(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        res = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
        return res.status_code == 200
    except: return False

def load_baseline(filename):
    try:
        res = requests.get(GITHUB_BASE + filename, timeout=10)
        if res.status_code == 200: return res.json()
    except: return None

def fetch_data(w3):
    lp_con = w3.eth.contract(address=w3.to_checksum_address(ADDR_LP_POOL), abi=ABI)
    is_u0 = lp_con.functions.token0().call().lower() == ADDR_USDT.lower()
    real_wkey = lp_con.functions.token1().call() if is_u0 else lp_con.functions.token0().call()
    w_con, u_con = w3.eth.contract(address=real_wkey, abi=ABI), w3.eth.contract(address=ADDR_USDT, abi=ABI)
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
        raw_w, raw_u = w_con.functions.balanceOf(t).call() / (10**dec), u_con.functions.balanceOf(t).call() / 1e18
        lp_bal = lp_con.functions.balanceOf(t).call()
        share = lp_bal / lp_supply if lp_supply > 0 else 0
        f_w, f_u = raw_w + ((r_w * share) / (10**dec)), raw_u + ((r_u * share) / 1e18)
        snap[name] = {"w": f_w, "u": f_u}
        if any(x in name for x in ["국고", "LP"]): total_u += f_u
        
    ratio = (snap["스테이킹 (자산동결)"]["w"] / total_supply * 100) if total_supply > 0 else 0
    snap["META"] = {"backing": total_u / total_supply if total_supply > 0 else 0, "supply": total_supply, "ratio": ratio, "tr_u": total_u, "price": price}
    return snap

def build_report(curr, base, mode_label="자정", all_mode=False):
    m, bm = curr["META"], base.get("META", curr["META"])
    pd, pp = m["price"] - bm["price"], ((m["price"] - bm["price"]) / bm["price"] * 100) if bm["price"] > 0 else 0
    ud, up = m["tr_u"] - bm["tr_u"], ((m["tr_u"] - bm["tr_u"]) / bm["tr_u"] * 100) if bm["tr_u"] > 0 else 0
    bd, bp = m["backing"] - bm["backing"], ((m["backing"] - bm["backing"]) / bm["backing"] * 100) if bm["backing"] > 0 else 0
    sd, sp = m["supply"] - bm["supply"], ((m["supply"] - bm["supply"]) / bm["supply"] * 100) if bm["supply"] > 0 else 0
    rd = m["ratio"] - bm["ratio"] 
    
    p_emo = "📈" if pd >= 0 else "📉"
    b_emo = "📈" if bp >= 0 else "📉"
    s_emo = "📈" if sd >= 0 else "📉"
    r_emo = "📈" if rd >= 0 else "📉"
    
    L = "━━━━━━━━━━━━━━━━━━━━━━━━"
    BAR = " ▬"
    
    res = f"<b>🤖 WebKeyDAO 관제 v6.2.7 ({RUN_FROM})</b>\n"
    res += f"<b>$</b> 시세: <b>${m['price']:.2f}</b> [<b>{pd:+.2f} ({pp:+.2f}%)</b>] {p_emo}{BAR}\n"
    res += f"💎 담보: <b>${m['backing']:.3f}</b> (<b>{bp:+.2f}%</b>) {b_emo}{BAR}\n"
    res += f"📊 발행: <b>{sd:+,.0f} ({sp:+.2f}%)</b> {s_emo} | 🔒 락업: <b>{m['ratio']:.1f}% ({rd:+.2f}%p)</b> {r_emo}\n"
    res += f"📉 기준: 깃허브 {mode_label} 데이터 기반 수사\n{L}\n"
    
    for n, _ in TARGETS:
        if not all_mode and n not in ["유동성 LP (시세결정)", "유동성 국고 (현금담보)", "트레저리 (발행원천)", "스테이킹 (자산동결)"]: continue
        c, b = curr[n], base.get(n, curr[n])
        wd = c['w'] - b['w']
        wp = (wd / b['w'] * 100) if b['w'] > 0 else 0
        res += f"📌 <b>{n}</b>\n • WKEY: {c['w']:,.0f} [<b>{wd:+,.0f} ({wp:+.1f}%)</b>]{BAR}\n"
        if c['u'] > 0.1:
            uds = c['u'] - b['u']
            up_ind = (uds / b['u'] * 100) if b['u'] > 0 else 0
            res += f" • USDT: <b>${c['u']:,.0f}</b> [<b>${uds:+,.0f} ({up_ind:+.1f}%)</b>]{BAR}\n"
        res += f"{L}\n"
    
    final_res = res + f"💰 총 가용현금: <b>${m['tr_u']:,.0f}</b> [<b>${ud:+,.0f} ({up:+.2f}%)</b>]{BAR}"
    if alert_history:
        final_res += f"\n\n🚨 <b>오늘의 유출 기록 (누적)</b>\n" + "\n".join(alert_history)
    return final_res

# ---------------------------------------------------------
# [3] 메인 루프 (Web3 타임아웃 20초 적용)
# ---------------------------------------------------------
if __name__ == "__main__":
    # RPC 응답이 없을 때 무한 대기를 방지하기 위해 타임아웃 강제 설정
    w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={'timeout': 20}))
    
    if w3.is_connected():
        current_day = str(datetime.date.today())
        init_curr = fetch_data(w3)
        init_synced = load_baseline(DAILY_FILE)
        
        if init_synced and init_synced.get("date") == current_day:
            daily_base = init_synced["data"]
            daily_label = "자정"
        else:
            daily_base = init_curr
            daily_label = "봇 가동 시점"

        last_alerted_usdt = daily_base["META"]["tr_u"]
        send_msg(f"🚀 <b>관제 v6.2.7 가동 ({RUN_FROM})</b>\n📍 기준: {daily_label} 데이터 동기화")
        
        off = 0
        while True:
            try:
                curr_data = fetch_data(w3)
                current_u = curr_data["META"]["tr_u"]
                if last_alerted_usdt - current_u > ALARM_LIMIT_USDT_OUT:
                    drop = last_alerted_usdt - current_u
                    incident = f"• {datetime.datetime.now().strftime('%H:%M')} : <b>${drop:,.0f}</b> 유출 ({RUN_FROM}) 🚨"
                    alert_history.append(incident)
                    send_msg(f"🚨 <b>[긴급: 추가 유출 발생 - {RUN_FROM}]</b>\n" + "\n".join(alert_history))
                    last_alerted_usdt = current_u

                up_res = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates", params={"offset": off, "timeout": 2}).json()
                for up in up_res.get("result", []):
                    off = up["update_id"] + 1
                    msg = up.get("message", {}).get("text", "").lower().strip()
                    if not msg: continue
                    is_all = "all" in msg

                    if any(x in msg for x in ["보고서", "일간", "daily", "all"]) and not any(x in msg for x in ["주간", "weekly", "월간", "monthly"]):
                        send_msg(build_report(curr_data, daily_base, daily_label, is_all))
                    elif any(x in msg for x in ["주간", "weekly"]):
                        w_data = load_baseline(WEEKLY_FILE)
                        if w_data: send_msg(build_report(curr_data, w_data["data"], "주간", is_all))
                        else: send_msg("⚠️ 깃허브에 주간 데이터가 없습니다.")
                    elif any(x in msg for x in ["월간", "monthly"]):
                        m_data = load_baseline(MONTHLY_FILE)
                        if m_data: send_msg(build_report(curr_data, m_data["data"], "월간", is_all))
                        else: send_msg("⚠️ 깃허브에 월간 데이터가 없습니다.")
                time.sleep(5)
            except Exception as e:
                time.sleep(10)
