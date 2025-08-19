//센서코드
#include <WiFi.h>
#include <HTTPClient.h>

// 🟢 Wi-Fi 설정
const char* ssid = "jmc";  // 오픈 네트워크
const char* password = "123456789";   // ✅ 추가됨
const char* serverUrl = "http://172.20.10.13:5000/upload";  // 🛑 여기를 바꾸세요

// 🟢 센서 연결 핀 설정
#define RXD2 17  // 센서 TX 연결
#define TXD2 16  // 사용 안함

// 🟢 센서 데이터 변수
String Temp, Humi, CO2eq, TVOC, PM2, PM3;

// 🟢 재연결 관련
unsigned long lastReconnectAttempt = 0;
const unsigned long reconnectInterval = 10000;  // 10초

// 🟢 센서 문자열 파싱
void splitString(String data) {
  int index = 0;
  String values[7];  // 최대 7개

  while (data.length() > 0 && index < 7) {
    int commaIndex = data.indexOf(",");
    if (commaIndex == -1) {
      values[index] = data;
      break;
    } else {
      values[index] = data.substring(0, commaIndex);
      data = data.substring(commaIndex + 1);
    }
    index++;
  }

  Temp  = values[0];
  Humi  = values[1];
  CO2eq = values[2];
  TVOC  = values[3];
  PM2   = values[5];  // values[4] → PM1 (출력 제외)
  PM3   = values[6];
}

// 🟢 센서 데이터 전송
void sendSensorData() {
  if (Temp == "" || Humi == "" || CO2eq == "" || TVOC == "" || PM2 == "" || PM3 == "") {
    Serial.println("❌ 전송 실패: 값 누락");
    return;
  }

  String payload = Temp + "," + Humi + "," + CO2eq + "," + TVOC + "," + PM2 + "," + PM3;
  Serial.println("📦 센서 데이터: " + payload);

  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient http;
    http.begin(serverUrl);
    http.addHeader("Content-Type", "application/json");

    String json = "{\"sensor_data\": \"" + payload + "\"}";

    int httpResponseCode = http.POST(json);
    if (httpResponseCode > 0) {
      Serial.printf("✅ 데이터 전송 성공 (%d)\n", httpResponseCode);
    } else {
      Serial.printf("❌ 전송 실패: %s\n", http.errorToString(httpResponseCode).c_str());
    }

    http.end();
  } else {
    Serial.println("⚠️ Wi-Fi 연결 안됨 → 전송 보류");
  }
}

// 🟢 네트워크 정보 출력
void get_network_info() {
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n[*] 네트워크 정보:");
    Serial.println("[+] SSID       : " + String(ssid));
    Serial.println("[+] BSSID      : " + WiFi.BSSIDstr());
    Serial.print("[+] 게이트웨이 : "); Serial.println(WiFi.gatewayIP());
    Serial.print("[+] 서브넷     : "); Serial.println(WiFi.subnetMask());
    Serial.print("[+] RSSI       : "); Serial.print(WiFi.RSSI()); Serial.println(" dBm");
    Serial.print("[+] 로컬 IP    : "); Serial.println(WiFi.localIP());
    Serial.println("--------------------------------------------------");
  }
}

// 🟢 Wi-Fi 연결 시도
void connectToWiFi() {
  Serial.println("\n[*] Wi-Fi 연결 시도 중...");
  WiFi.begin(ssid, password);

  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 20) {
    Serial.print(".");
    delay(500);
    tries++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n✅ 연결 성공!");
    get_network_info();
  } else {
    Serial.println("\n❌ 연결 실패");
  }
}

// 🟢 setup
void setup() {
  Serial.begin(115200);
  delay(1000);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true);  // 이전 정보 초기화
  connectToWiFi();

  Serial2.begin(9600, SERIAL_8N1, RXD2, TXD2);  // UART2
}

// 🟢 loop
void loop() {
  // Wi-Fi 끊김 감지
  if (WiFi.status() != WL_CONNECTED) {
    unsigned long now = millis();
    if (now - lastReconnectAttempt > reconnectInterval) {
      Serial.println("\n⚠️ Wi-Fi 끊김 감지 → 재연결 시도");
      connectToWiFi();
      lastReconnectAttempt = now;
    }
  }

  // 센서 데이터 읽기
  if (Serial2.available()) {
    String receivedData = Serial2.readStringUntil('\n');
    receivedData.trim();
    splitString(receivedData);
    sendSensorData();
  }

  delay(6000);
}
