import sys, time, requests, json, datetime, os, threading
from web3 import Web3
from flask import Flask

# ---------------------------------------------------------
# [1] 설정 (PHI 전용 데이터로 교체 완료)
# ---------------------------------------------------------
TELEGRAM_TOKEN = "8499432639:AAFp7aLo3Woum2FeAA23kJTKFDMCZ0rMqM8"
CHAT_ID = "-5074742053"

# BSC RPC 노드
RPC_NODES = [
    "https://bsc-dataseed1.binance.org/",
    "https://binance.llamarpc.com",
    "https://bscrpc.com"
]

# 깃허브 데이터 경로 (방금 만드신 phi_daily_data.json 연결)
GITHUB_BASE = "https://raw.githubusercontent.com/hyoungyoublee/webkey-monitor/main/"
DAILY_FILE = "phi_daily_data.json"

# PHI 핵심 주소 설정
ADDR_PHI      = "0xa71add46ea4fbf0058b36e6baa39530f8e48b103"
ADDR_USDT     = "0x55d398326f99059fF775485246999027B3197955"
ADDR_LP_POOL  = "0x4fe1e52788cd0d36781924707a454110b421dc68" # 시세 산출 기준점

# 감시 대상 리스트 (제공해주신 7개 주소)
TARGETS = [
    ("PHI 메인 계약", ADDR_PHI),
    ("PHI 스테이킹", "0x58115a09a3083e935464c9f79a4605b9a152bc85"),
    ("DAO 풀 (거버넌스)", "0x367cd19894e49ea4b1cd72bf34657e21db12d26a"),
    ("팟 (Pot 자금)", "0xb9fe6c82a37a5fee8b67906a09650883953c114e"),
    ("참여보상 해제 풀", "0x02a4a677d3080a458d2cff610bb14bc52dacbaac"),
    ("LP 채권 계약", "0x4fe1e52788cd0d36781924707a454110b421dc68"),
    ("보상금 분배 계약", "0xe80e7960221e2f50626a232ec62209774f79a44b")
]

# 표준 ABI
ABI = [{"constant":True,"inputs":[],"name":"token0","outputs":[{"name":"","type":"address"}],"type":"function"},{"constant":True,"inputs":[],"name":"token1","outputs":[{"name":"","type":"address"}],"type":"function"},{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},{"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},{"constant":True,"inputs":[],"name":"getReserves","outputs":[{"name":"_reserve0","type":"uint112"},{"name":"_reserve1","type":"uint112"},{"name":"_blockTimestampLast","type":"uint32"}],"type":"function"},{"constant":True,"inputs":[],"name":"totalSupply","outputs":[{"name":"total","type":"uint256"}],"type":"function"}]

LATEST_SNAP = None
CACHE_LOCK = threading.Lock()

# Replit 생존용 웹서버
app = Flask('')
@app.route('/')
def home(): return "🤖 PHI Monitor is Online!"
def run_web_server(): app.run(host='0.0.0.0', port=8080)

# ---------------------------------------------------------
# [2] 핵심 엔진 로직
# ---------------------------------------------------------
def get_emo(v): return "📈" if v > 0.0001 else ("📉" if v < -0.0001 else "▬")

def send_msg(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
    except: pass

def get_w3():
    for url in RPC_NODES:
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={'timeout': 5}))
            if w3.is_connected(): return w3
        except: continue
    return None

def fetch_now():
    w3 = get_w3()
    if not w3: return None
    try:
        phi_con = w3.eth.contract(address=w3.to_checksum_address(ADDR_PHI), abi=ABI)
        u_con = w3.eth.contract(address=w3.to_checksum_address(ADDR_USDT), abi=ABI)
        lp_con = w3.eth.contract(address=w3.to_checksum_address(ADDR_LP_POOL), abi=ABI)
        
        dec = phi_con.functions.decimals().call()
        total_s = phi_con.functions.totalSupply().call() / (10**dec)
        
        # 시세 계산 (LP)
        try:
            res = lp_con.functions.getReserves().call()
            is_u0 = lp_con.functions.token0().call().lower() == ADDR_USDT.lower()
            r_u, r_p = (res[0], res[1]) if is_u0 else (res[1], res[0])
            now_p = (r_u / 1e18) / (r_p / (10**dec)) if r_p > 0 else 0.0
        except: now_p = 0.0

        snap = {}
        for name, addr in TARGETS:
            try:
                target_addr = w3.to_checksum_address(addr.strip())
                b_phi = phi_con.functions.balanceOf(target_addr).call() / (10**dec)
                b_u = u_con.functions.balanceOf(target_addr).call() / 1e18
                snap[name] = {"phi": b_phi, "u": b_u}
            except: snap[name] = {"phi": 0, "u": 0}

        # 올림푸스 핵심 지표: 담보가치 (DAO풀 + LP채권의 USDT 기반)
        tr_u = snap["DAO 풀 (거버넌스)"]["u"] + snap["LP 채권 계약"]["u"]
        coll_val = tr_u / total_s if total_s > 0 else 0

        snap["META"] = {
            "supply": total_s, "tr_u": tr_u, "price": now_p, 
            "st_ratio": (snap["PHI 스테이킹"]["phi"] / total_s * 100) if total_s > 0 else 0, 
            "coll_val": coll_val, "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        return snap
    except Exception as e:
        print(f"Error: {e}")
        return None

def build_pro_report(curr, base_data, mode="자정"):
    if not curr: return "⚠️ 데이터 수집 중..."
    try:
        base = base_data.get("data", base_data)
        b_date = base_data.get("date", "정보없음")
        m, bm = curr["META"], base.get("META", {})

        # 변동치 계산
        pd = m["price"] - bm.get("price", m["price"])
        sd = m["supply"] - bm.get("supply", m["supply"])
        rd = m["st_ratio"] - bm.get("st_ratio", m["st_ratio"])
        cvd = m["coll_val"] - bm.get("coll_val", m["coll_val"])

        L = "━━━━━━━━━━━━━━━━━━━━━━━━"
        res = f"<b>🏛️ PHI Olympus Fact {mode} 리포트</b>\n"
        res += f"<b>$</b> 시세: <b>${m['price']:.4f}</b> [<b>{pd:+.4f}</b>] {get_emo(pd)}\n"
        res += f"📊 발행: <b>{m['supply']:,.0f}</b> [<b>{sd:+,.0f}</b>]\n"
        res += f"🔒 스테이킹: <b>{m['st_ratio']:.1f}%</b> [<b>{rd:+.1f}%</b>]\n"
        res += f"🛡️ 담보: <b>${m['coll_val']:.4f}</b> [<b>{cvd:+.4f}</b>]\n"
        res += f"📉 기준: {b_date} 대비\n{L}\n"

        # 상세 지갑 (주요 4개만 요약 표시)
        for n in ["PHI 스테이킹", "DAO 풀 (거버넌스)", "LP 채권 계약", "팟 (Pot 자금)"]:
            c, b = curr[n], base.get(n, curr[n])
            wd = c['phi'] - b.get('phi', c['phi'])
            res += f"📌 <b>{n}</b>\n"
            res += f" • PHI: {c['phi']:,.0f} [<b>{wd:+,.0f}</b>]\n"
            res += f" • USDT: <b>${c['u']:,.0f}</b>\n{L}\n"
        
        return res + f"💰 DAO 가용현금: <b>${m['tr_u']:,.0f}</b>"
    except: return "⚠️ 리포트 생성 중 오류"

def safe_get(file, curr=None):
    try: 
        r = requests.get(f"{GITHUB_BASE}{file}?t={int(time.time())}", timeout=8)
        return r.json() if r.status_code == 200 else {"data": curr, "date": "초기데이터"}
    except: return {"data": curr, "date": "연결실패"}

# ---------------------------------------------------------
# [3] 실행부
# ---------------------------------------------------------
def monitor_worker():
    global LATEST_SNAP
    while True:
        try:
            snap = fetch_now()
            if snap:
                with CACHE_LOCK: LATEST_SNAP = snap
        except: pass
        time.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    LATEST_SNAP = fetch_now()
    
    if LATEST_SNAP:
        send_msg(f"🚀 <b>PHI Olympus Fact 관제 부팅 성공</b>\nv1.0 주소 업데이트 완료")
        send_msg(build_pro_report(LATEST_SNAP, safe_get(DAILY_FILE, LATEST_SNAP)))

    threading.Thread(target=monitor_worker, daemon=True).start()

    off = 0
    while True:
        try:
            up_res = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates", params={"offset": off, "timeout": 10}).json()
            for up in up_res.get("result", []):
                off = up["update_id"] + 1
                msg = up.get("message", {}).get("text", "").lower()
                with CACHE_LOCK: curr = LATEST_SNAP or fetch_now()
                send_msg(build_pro_report(curr, safe_get(DAILY_FILE, curr), "실시간"))
            time.sleep(0.5)
        except: time.sleep(5)
