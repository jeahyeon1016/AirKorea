import requests
from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import threading
import urllib.parse
import time

app = Flask(__name__)
CORS(app)

# ===================== 한국시간(KST) 유틸 =====================
KST = ZoneInfo("Asia/Seoul")

def to_kst_str(dt, fmt="%Y-%m-%d %H:%M:%S"):
    """
    DB에는 UTC(naive)로 저장.
    출력 시에는 항상 한국시간(Asia/Seoul)으로 변환해서 문자열로 반환.
    """
    if dt is None:
        return None
    # naive -> UTC 가정
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).strftime(fmt)

def tag_kst(dt):
    """
    에어코리아 timestamp 등 'KST 기준의 naive datetime'을
    명시적으로 KST로 태깅해서 보관하려는 용도.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt.astimezone(KST)

# ===================== 외부(동국) API 설정 =====================
DONGUK_API_URL = "http://144.24.86.225:8083/dongukSpeed"
DONGUK_KEY = "6AhrGb8HFc7sNmg3B3hY8Fo3QvNa6HxoMG2K1YsbyAqagQ2Un5x3HTkuyt13pKD7n6vVFKZBjr2Fni4ZJBQyogKM8Htamtrb4y5H"

def send_donguk_speed(speed: int):
    """speed: 0~3"""
    if not isinstance(speed, int) or speed < 0 or speed > 3:
        return {"ok": False, "status": None, "text": "speed must be an integer 0~3"}
    headers = {"donguk_key": DONGUK_KEY}
    payload = {"speed": speed}
    try:
        print(f"[동국 API 전송 시도] {payload}")
        resp = requests.post(DONGUK_API_URL, headers=headers, json=payload, timeout=5)
        print(f"[동국 API 응답] status={resp.status_code}, text={resp.text}")
        return {"ok": resp.ok, "status": resp.status_code, "text": resp.text}
    except Exception as e:
        print(f"[동국 API 오류] {e}")
        return {"ok": False, "status": None, "text": str(e)}

# ===================== DB 연결 설정 =====================
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:ckdudwns%40%401@127.0.0.1:3306/environmental_monitoring'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# ===================== DB 모델 =====================
class SensorData(db.Model):
    __tablename__ = 'sensor_data'
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    temperature = db.Column(db.Numeric(5, 2))
    humidity = db.Column(db.Numeric(5, 2))
    co2eq = db.Column(db.Integer)
    tvoc = db.Column(db.Numeric(10, 3))
    pm1_0 = db.Column(db.Numeric(8, 3))
    pm2_5 = db.Column(db.Numeric(8, 3))
    pm10 = db.Column(db.Numeric(8, 3))
    # 저장은 UTC naive
    measured_at = db.Column(db.TIMESTAMP, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "temperature": float(self.temperature) if self.temperature is not None else None,
            "humidity": float(self.humidity) if self.humidity is not None else None,
            "co2eq": self.co2eq,
            "tvoc": float(self.tvoc) if self.tvoc is not None else None,
            "pm1_0": float(self.pm1_0) if self.pm1_0 is not None else None,
            "pm2_5": float(self.pm2_5) if self.pm2_5 is not None else None,
            "pm10": float(self.pm10) if self.pm10 is not None else None,
            # 출력은 KST 문자열
            "measured_at": to_kst_str(self.measured_at)
        }

class EnvironmentScore(db.Model):
    __tablename__ = 'environmental_scores'
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    sensor_id = db.Column(db.BigInteger, db.ForeignKey('sensor_data.id'), nullable=False)
    environmental_score = db.Column(db.Numeric(5, 2), nullable=False)
    created_at = db.Column(db.TIMESTAMP, default=datetime.utcnow)
    sensor_data = db.relationship('SensorData', backref=db.backref('scores', lazy=True))

class AirKoreaData(db.Model):
    __tablename__ = 'air_korea_data'
    timestamp = db.Column(db.DateTime, primary_key=True)  # KST로 태깅해 저장(naive)
    pm10_ug_m3_ = db.Column(db.Numeric(8, 3))
    pm2_5_ug_m3_ = db.Column(db.Numeric(8, 3))
    pm10_category = db.Column(db.Integer)
    pm2_5_category = db.Column(db.Integer)
    o3_ppm_ = db.Column(db.Numeric(5, 2))
    no2_ppm_ = db.Column(db.Numeric(5, 2))
    co_ppm_ = db.Column(db.Numeric(5, 2))
    so2_ppm_ = db.Column(db.Numeric(5, 2))

# ===================== 점수 계산 =====================
class AirQualityEvaluator:
    def __init__(self, pm25_value, pm10_value):
        self.pm25_value = pm25_value
        self.pm10_value = pm10_value
        self.category_priority = {"좋음": 1, "보통": 2, "나쁨": 3, "매우 나쁨": 4}
        self.final_score_map = {"좋음": 1, "보통": 2, "나쁨": 3, "매우 나쁨": 4}

    def get_pm25_category(self):
        v = self.pm25_value
        if v <= 15: return "좋음"
        if v <= 35: return "보통"
        if v <= 75: return "나쁨"
        return "매우 나쁨"

    def get_pm10_category(self):
        v = self.pm10_value
        if v <= 30: return "좋음"
        if v <= 80: return "보통"
        if v <= 150: return "나쁨"
        return "매우 나쁨"

    def evaluate(self):
        c25 = self.get_pm25_category()
        c10 = self.get_pm10_category()
        final = c25 if self.category_priority[c25] >= self.category_priority[c10] else c10
        return self.final_score_map[final]

def calculate_environmental_score(pm2_5, pm10):
    evaluator = AirQualityEvaluator(float(pm2_5), float(pm10))
    return evaluator.evaluate()

def process_environment_score(sensor_data):
    score = calculate_environmental_score(sensor_data.pm2_5 or 0, sensor_data.pm10 or 0)
    db.session.add(EnvironmentScore(sensor_id=sensor_data.id, environmental_score=score))
    db.session.commit()
    return score

# 점수(1~4) → 동국 speed(0~3)
def map_score_to_speed(score) -> int:
    try:
        s = int(round(float(score)))
        return max(0, min(3, s - 1))
    except Exception:
        return 0

# ===================== 유틸 =====================
def _to_float(s):
    if s is None: return None
    s = str(s).strip()
    if s in ("", "-", "nan", "NaN", "null", "None"): return None
    try:
        return float(s)
    except Exception:
        return None

def _to_int(s):
    v = _to_float(s)
    return int(v) if v is not None else None

# ===================== ESP32 업로드 엔드포인트 (CSV 전용 안전 버전) =====================
# ESP32: {"sensor_data": "Temp,Humi,CO2eq,TVOC,PM2,PM3"}
@app.route("/upload", methods=["POST"])
def upload_from_esp32():
    # Content-Type 확인
    if not request.is_json:
        return jsonify({"success": False, "error": "Content-Type must be application/json"}), 415
    try:
        body = request.get_json(silent=False) or {}
    except Exception:
        return jsonify({"success": False, "error": "invalid JSON body"}), 400

    try:
        csv_line = body.get("sensor_data", "")
        parts = [p.strip() for p in str(csv_line).split(",")]

        if len(parts) != 6:
            return jsonify({"success": False, "error": f"invalid payload (need 6 fields): {csv_line}"}), 400

        temperature = _to_float(parts[0])
        humidity    = _to_float(parts[1])
        co2eq       = _to_int(parts[2])
        tvoc        = _to_float(parts[3])
        pm2_5       = _to_float(parts[4])
        pm10        = _to_float(parts[5])

        rec = SensorData(
            temperature=temperature,
            humidity=humidity,
            co2eq=co2eq,
            tvoc=tvoc,
            pm2_5=pm2_5,
            pm10=pm10,
            # 저장은 UTC naive
            measured_at=datetime.utcnow(),
        )
        db.session.add(rec)
        db.session.commit()

        # PM 둘 다 있을 때만 점수 계산 + 동국 API 자동 전송
        score = None
        donguk_result = None
        if rec.pm2_5 is not None and rec.pm10 is not None:
            score = float(process_environment_score(rec))
            speed = map_score_to_speed(score)  # 1~4 -> 0~3
            donguk_result = send_donguk_speed(speed)

        return jsonify({
            "success": True,
            "sensor_data": rec.to_dict(),
            "environmental_score": score,
            "donguk": donguk_result
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

# ===================== 기존 REST (유지) =====================
@app.route("/api/sensor_data", methods=["GET", "POST"])
def sensor_data_endpoint():
    if request.method == "POST":
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "JSON 데이터를 받지 못함"}), 400
        rec = SensorData(
            temperature=data.get("temperature"),
            humidity=data.get("humidity"),
            co2eq=data.get("co2eq"),
            tvoc=data.get("tvoc"),
            pm1_0=data.get("pm1_0"),
            pm2_5=data.get("pm2_5"),
            pm10=data.get("pm10")
        )
        db.session.add(rec)
        db.session.commit()

        score = None
        donguk_result = None
        if rec.pm2_5 is not None and rec.pm10 is not None:
            score = float(process_environment_score(rec))
            speed = map_score_to_speed(score)
            donguk_result = send_donguk_speed(speed)

        return jsonify({
            "success": True,
            "sensor_data": rec.to_dict(),
            "environmental_score": score,
            "donguk": donguk_result
        }), 201
    else:
        all_data = SensorData.query.order_by(SensorData.measured_at.desc()).all()
        return jsonify({"success": True, "sensor_data": [d.to_dict() for d in all_data]}), 200

@app.route("/api/scores", methods=["GET"])
def get_scores():
    try:
        scores = EnvironmentScore.query.order_by(EnvironmentScore.created_at.desc()).all()
        return jsonify({
            "success": True,
            "scores": [{
                "id": s.id,
                "score": float(s.environmental_score),
                # 출력은 KST
                "calculated_at": to_kst_str(s.created_at),
                "sensor_data": s.sensor_data.to_dict() if s.sensor_data else None
            } for s in scores]
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ===================== 동국 API 수동 제어 엔드포인트 =====================
@app.route("/api/device/speed", methods=["POST"])
def set_device_speed():
    """
    Body(JSON): {"speed": 0..3}
    """
    try:
        payload = request.get_json(silent=True) or {}
        speed = payload.get("speed", None)
        if isinstance(speed, str) and speed.isdigit():
            speed = int(speed)
        if not isinstance(speed, int):
            return jsonify({"success": False, "error": "speed(0~3) 정수로 보내세요."}), 400
        if speed < 0 or speed > 3:
            return jsonify({"success": False, "error": "speed는 0~3 범위입니다."}), 400

        result = send_donguk_speed(speed)
        return jsonify({"success": result["ok"], "result": result}), (200 if result["ok"] else 502)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ===================== 에어코리아 =====================
def fetch_and_save_airkorea_data():
    SERVICE_KEY = "tlBcA73yJuLT1PSGixHpbHwLcINQEVtZ0g5xfd2E5/+qZUSmPK1hSFACjbw+pauS2glnKPhOPUcniVoBRkGfpA=="
    params = {
        'serviceKey': urllib.parse.unquote(SERVICE_KEY),
        'stationName': '삼천동',
        'dataTerm': 'DAILY',
        'pageNo': '1',
        'numOfRows': '1',
        'returnType': 'json',
        'ver': '1.3'
    }
    url = "http://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty"
    try:
        # 공공API 인증서 관련 경고 회피 위해 verify=False, 운영에서는 인증서 구성 권장
        response = requests.get(url, params=params, verify=False, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data['response']['header']['resultCode'] == "00":
                item = data['response']['body']['items'][0]
                # 에어코리아는 KST 기준 시각 제공 -> 명시적으로 KST 태깅
                timestamp_kst = tag_kst(datetime.strptime(item['dataTime'], "%Y-%m-%d %H:%M"))
                # PK 충돌 방지를 위해 동일 시각 중복 저장 금지
                if not AirKoreaData.query.filter_by(timestamp=timestamp_kst.replace(tzinfo=None)).first():
                    record = AirKoreaData(
                        timestamp=timestamp_kst.replace(tzinfo=None),  # DB에는 naive로 저장
                        pm10_ug_m3_=float(item['pm10Value']) if item['pm10Value'] != "-" else None,
                        pm2_5_ug_m3_=float(item['pm25Value']) if item['pm25Value'] != "-" else None,
                        pm10_category=int(item.get('pm10Grade')) if item.get('pm10Grade') and str(item.get('pm10Grade')).isdigit() else None,
                        pm2_5_category=int(item.get('pm25Grade')) if item.get('pm25Grade') and str(item.get('pm25Grade')).isdigit() else None,
                        o3_ppm_=float(item['o3Value']) if item['o3Value'] != "-" else None,
                        no2_ppm_=float(item['no2Value']) if item['no2Value'] != "-" else None,
                        co_ppm_=float(item['coValue']) if item['coValue'] != "-" else None,
                        so2_ppm_=float(item['so2Value']) if item['so2Value'] != "-" else None
                    )
                    db.session.add(record)
                    db.session.commit()
                    print("✅ 에어코리아 데이터 저장 완료")
                else:
                    print("ℹ️ 이미 저장된 데이터입니다.")
            else:
                print("❌ API 오류:", data['response']['header']['resultMsg'])
        else:
            print("❌ HTTP 오류:", response.status_code)
    except Exception as e:
        print("❌ 요청 실패:", str(e))

@app.route("/api/airkorea", methods=["GET"])
def get_airkorea_data():
    try:
        data = AirKoreaData.query.order_by(AirKoreaData.timestamp.desc()).limit(10).all()
        result = [{
            # 저장된 값은 KST naive로 간주 -> 명시적으로 KST 태깅 후 포맷
            "timestamp": to_kst_str(tag_kst(d.timestamp), fmt="%Y-%m-%d %H:%M"),
            "pm10": float(d.pm10_ug_m3_) if d.pm10_ug_m3_ is not None else None,
            "pm2_5": float(d.pm2_5_ug_m3_) if d.pm2_5_ug_m3_ is not None else None,
            "pm10_category": d.pm10_category,
            "pm2_5_category": d.pm2_5_category
        } for d in data]
        return jsonify({"success": True, "data": result}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ===================== 백그라운드 태스크 & 대시보드 =====================
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True}), 200

def background_sensor_task():
    while True:
        print("[백그라운드] 에어코리아 데이터 수집 중...")
        with app.app_context():
            fetch_and_save_airkorea_data()
        time.sleep(3600)  # 1시간

@app.route("/dashboard")
def show_dashboard():
    # 최신 20개 센서 데이터
    recent_data = SensorData.query.order_by(SensorData.measured_at.desc()).limit(20).all()
    sensor_data = []
    for s in recent_data:
        score = s.scores[0].environmental_score if s.scores else None
        sensor_data.append({
            # 출력은 KST(분 단위)
            "measured_at": to_kst_str(s.measured_at, fmt="%Y-%m-%d %H:%M"),
            "temperature": float(s.temperature) if s.temperature is not None else None,
            "humidity": float(s.humidity) if s.humidity is not None else None,
            "co2eq": s.co2eq,
            "tvoc": float(s.tvoc) if s.tvoc is not None else None,
            "pm2_5": float(s.pm2_5) if s.pm2_5 is not None else None,
            "pm10": float(s.pm10) if s.pm10 is not None else None,
            "environmental_score": float(score) if score is not None else None
        })

    # 에어코리아 최근 10개
    air_data = AirKoreaData.query.order_by(AirKoreaData.timestamp.desc()).limit(10).all()
    air_korea = []
    for a in air_data:
        air_korea.append({
            "timestamp": to_kst_str(tag_kst(a.timestamp), fmt="%Y-%m-%d %H:%M"),
            "pm10": float(a.pm10_ug_m3_) if a.pm10_ug_m3_ is not None else None,
            "pm2_5": float(a.pm2_5_ug_m3_) if a.pm2_5_ug_m3_ is not None else None,
            "pm10_category": a.pm10_category,
            "pm2_5_category": a.pm2_5_category,
            "o3": float(a.o3_ppm_) if a.o3_ppm_ is not None else None,
            "no2": float(a.no2_ppm_) if a.no2_ppm_ is not None else None,
            "co": float(a.co_ppm_) if a.co_ppm_ is not None else None,
            "so2": float(a.so2_ppm_) if a.so2_ppm_ is not None else None
        })

    return render_template("dashboard.html", sensor_data=sensor_data, air_korea=air_korea)

# ===================== 실행 =====================
if __name__ == '__main__':
    threading.Thread(target=background_sensor_task, daemon=True).start()
    # ESP32에서 접속 가능하도록 0.0.0.0
    app.run(debug=True, host='0.0.0.0', port=5000)
