import sys, time, requests, json, datetime, os
from web3 import Web3

# ---------------------------------------------------------
# [1] 설정 및 12대 지갑 주소
# ---------------------------------------------------------
TELEGRAM_TOKEN = "8499432639:AAFp7aLo3Woum2FeAA23kJTKFDMCZ0rMqM8"
CHAT_ID = "-5074742053"
RPC_URL = "https://bsc-dataseed.binance.org/" 

GITHUB_RAW_URL = "https://raw.githubusercontent.com/hyoungyoublee/webkey-monitor/refs/heads/main/webkey_daily_data.json"

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
alert_history = []  # 오늘 발생한 유출 이력 누적 리스트
last_alerted_usdt = 0
ABI = [{"constant":True,"inputs":[],"name":"token0","outputs":[{"name":"","type":"address"}],"type":"function"},{"constant":True,"inputs":[],"name":"token1","outputs":[{"name":"","type":"address"}],"type":"function"},{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},{"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},{"constant":True,"inputs":[],"name":"getReserves","outputs":[{"name":"_reserve0","type":"uint112"},{"name":"_reserve1","type":"uint112"},{"name":"_blockTimestampLast","type":"uint32"}],"type":"function"},{"constant":True,"inputs":[],"name":"totalSupply","outputs":[{"name":"total","type":"uint256"}],"type":"function"}]

# ---------------------------------------------------------
# [2] 핵심 함수
# ---------------------------------------------------------

def send_msg(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        res = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
        return res.status_code == 200
    except Exception as e:
        print(f"❌ 텔레그램 전송 실패: {e}")
        return False

def load_synced_baseline():
    """깃허브에서 자정 기준 데이터를 강제로 긁어옴"""
    try:
        res = requests.get(GITHUB_RAW_URL, timeout=10)
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

def build_report(curr, base, all_mode=False, base_label="깃허브 자정 데이터"):
    m, bm = curr["META"], base.get("META", curr["META"])
    pd, pp = m["price"] - bm["price"], ((m["price"] - bm["price"]) / bm["price"] * 100) if bm["price"] > 0 else 0
    ud, up = m["tr_u"] - bm["tr_u"], ((m["tr_u"] - bm["tr_u"]) / bm["tr_u"] * 100) if bm["tr_u"] > 0 else 0
    bd, bp = m["backing"] - bm["backing"], ((m["backing"] - bm["backing"]) / bm["backing"] * 100) if bm["backing"] > 0 else 0
    
    L = "━━━━━━━━━━━━━━━━━━━━━━━━"
    res = f"<b>🤖 WebKeyDAO 관제 v6.2.6 (검증모드)</b>\n"
    res += f"💲 시세: <b>${m['price']:.2f}</b> [<b>{pd:+.2f} ({pp:+.2f}%)</b>]\n"
    res += f"💎 담보: <b>${m['backing']:.3f}</b> (<b>{bp:+.2f}%</b>)\n"
    res += f"📉 기준: {base_label} 기반 수사\n{L}\n"
    
    for n, _ in TARGETS:
        if not all_mode and n not in ["유동성 LP (시세결정)", "유동성 국고 (현금담보)", "트레저리 (발행원천)", "스테이킹 (자산동결)"]: continue
        c, b = curr[n], base.get(n, curr[n])
        wd = c['w'] - b['w']
        res += f"📌 <b>{n}</b>\n • WKEY: {c['w']:,.0f} [<b>{wd:+,.0f}</b>]\n"
        if c['u'] > 1:
            uds = c['u'] - b['u']
            res += f" • USDT: <b>${c['u']:,.0f}</b> [<b>${uds:+,.0f}</b>]\n"
        res += f"{L}\n"
    
    final_res = res + f"💰 <b>총 가용현금: ${m['tr_u']:,.0f}</b>"
    
    # [수정] 보고서 하단에 오늘 발생한 긴급 알람 이력 추가
    if alert_history:
        final_res += f"\n\n🚨 <b>오늘의 유출 기록 (누적)</b>\n" + "\n".join(alert_history)
        
    return final_res

def check_emergency_alarms(curr, base):
    global last_alerted_usdt, alert_history
    current_u = curr["META"]["tr_u"]
    now_time = datetime.datetime.now().strftime("%H:%M")
    
    if last_alerted_usdt == 0: 
        last_alerted_usdt = base["META"]["tr_u"]
    
    drop_amount = last_alerted_usdt - current_u
    
    # 설정한 한도(예: 5만불) 이상 유출 시 알람
    if drop_amount > ALARM_LIMIT_USDT_OUT:
        incident = f"• {now_time} : <b>${drop_amount:,.0f}</b> 유출 🚨"
        alert_history.append(incident) # 리스트에 시간대와 금액 저장
        
        msg = f"🚨 <b>[긴급: 추가 유출 발생]</b>\n📜 <b>오늘의 실시간 유출 목록</b>\n" + "\n".join(alert_history)
        send_msg(msg)
        last_alerted_usdt = current_u 

# ---------------------------------------------------------
# [3] 메인 루프 (엄격한 기준점 적용)
# ---------------------------------------------------------
if __name__ == "__main__":
    print("🔍 수사 엔진 기동 중...")
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    
    if w3.is_connected():
        current_day = str(datetime.date.today())
        init_curr = fetch_data(w3)
        
        # [수정] 깃허브 자정 데이터 로드 시도
        init_synced = load_synced_baseline()
        
        # 깃허브 데이터가 있고, 오늘 날짜인 경우만 기준점으로 사용
        if init_synced and init_synced.get("date") == current_day:
            init_base = init_synced["data"]
            label = "깃허브 자정 데이터"
            print(f"✅ 기준점 확립: {current_day} 자정 장부 동기화 완료")
        else:
            # 자정 데이터가 없으면 현재 데이터를 기준점으로 삼아 변동폭을 0으로 강제 초기화
            init_base = init_curr
            label = "봇 가동 시점 (자정 데이터 없음)"
            print("⚠️ 경고: 깃허브 자정 데이터가 없습니다. 변동폭 0으로 시작합니다.")

        last_alerted_usdt = init_base["META"]["tr_u"]
        
        # 가동 알림 및 첫 보고서 전송
        success = send_msg(f"🚀 <b>관제 v6.2.6 가동 (검증 완료)</b>\n기준: {label}")
        if success:
            send_msg(build_report(init_curr, init_base, False, label))
            print("🎉 모든 절차 완료! 텔레그램을 확인하십시오.")
        
        off = 0
        while True:
            try:
                curr_data = fetch_data(w3)
                check_emergency_alarms(curr_data, init_base)
                
                # 메시지 수신 확인
                up_res = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates", params={"offset": off, "timeout": 2}).json()
                for up in up_res.get("result", []):
                    off = up["update_id"] + 1
                    msg = up.get("message", {}).get("text", "").lower().strip()
                    if any(x in msg for x in ["보고서", "all"]):
                        # 보고서 생성 시 저장된 alert_history가 자동으로 포함됨
                        send_msg(build_report(curr_data, init_base, "all" in msg, label))
                time.sleep(5)
            except Exception as e: 
                print(f"⚠️ 루프 에러: {e}")
                time.sleep(10)
    else:
        print("❌ [에러] BSC 노드 연결 실패!")
