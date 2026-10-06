#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <LiquidCrystal_I2C.h>
#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>
#include <Wire.h>

const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* BILLING_API_URL = "http://192.168.137.1:5000/api/stats";
const char* BILLING_ACTION_BASE_URL = "http://192.168.137.1:5000";

constexpr uint8_t LCD_ADDRESS = 0x27;
constexpr uint8_t LCD_COLUMNS = 16;
constexpr uint8_t LCD_ROWS = 2;
constexpr int I2C_SDA_PIN = 21;
constexpr int I2C_SCL_PIN = 22;
constexpr int DEFAULT_LID_OPEN_ANGLE = 20;
constexpr int DEFAULT_LID_CLOSED_ANGLE = 110;
constexpr unsigned long STATUS_INTERVAL_MS = 2000;
constexpr unsigned long SCREEN_INTERVAL_MS = 3000;
constexpr unsigned long WIFI_RETRY_INTERVAL_MS = 10000;

LiquidCrystal_I2C lcd(LCD_ADDRESS, LCD_COLUMNS, LCD_ROWS);
Preferences preferences;
WebServer webServer(80);
unsigned long lastStatusRequest = 0;
unsigned long lastScreenChange = 0;
unsigned long lastWiFiRetry = 0;
int screenIndex = 0;
int lidOpenAngle = DEFAULT_LID_OPEN_ANGLE;
int lidClosedAngle = DEFAULT_LID_CLOSED_ANGLE;
bool pythonConnected = false;
String lastPythonError = "Not checked yet";

void printLine(uint8_t row, const String& text) {
  String padded = text;
  if (padded.length() > LCD_COLUMNS) padded = padded.substring(0, LCD_COLUMNS);
  while (padded.length() < LCD_COLUMNS) padded += ' ';
  lcd.setCursor(0, row);
  lcd.print(padded);
}

void showMessage(const String& first, const String& second) {
  lcd.clear();
  printLine(0, first);
  printLine(1, second);
}

void writeLidAngle(int angle) {
  (void)angle;
}

void setLidClosed(bool closed) {
  (void)closed;
}

String calibrationPage() {
  return R"HTML(<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>ESP32 Lid Calibration</title><style>body{margin:0;padding:22px;background:#eef3ef;color:#14251f;font:16px system-ui,sans-serif}main{max-width:520px;margin:auto;background:white;padding:22px;border-radius:18px;box-shadow:0 12px 35px #18352b22}h1{margin-top:0}label{display:block;margin:18px 0 6px;font-weight:700}input{width:100%}.value{font-family:monospace}.row{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}button{border:0;border-radius:10px;padding:12px 15px;background:#173f34;color:white;font-weight:700}button.secondary{background:#dcebe2;color:#173f34}.status{padding:12px;border-radius:10px;background:#f0f5f1;line-height:1.6}.ok{color:#197047}.bad{color:#a33d32}</style></head><body><main><h1>ESP32 Lid Calibration</h1><div class="status" id="status">Checking connection...</div><label>Open angle: <span class="value" id="openValue">20</span> deg</label><input id="open" type="range" min="0" max="180" value="20" oninput="openValue.textContent=this.value"><label>Closed angle: <span class="value" id="closedValue">110</span> deg</label><input id="closed" type="range" min="0" max="180" value="110" oninput="closedValue.textContent=this.value"><div class="row"><button onclick="move('open')">Move open</button><button onclick="move('close')">Move closed</button><button class="secondary" onclick="save()">Save angles</button><button class="secondary" onclick="resetAngles()">Reset defaults</button></div><p>Python website: <span id="python">checking</span></p></main><script>async function refresh(){let r=await fetch('/api/status');let d=await r.json();document.getElementById('open').value=d.openAngle;document.getElementById('closed').value=d.closedAngle;document.getElementById('openValue').textContent=d.openAngle;document.getElementById('closedValue').textContent=d.closedAngle;document.getElementById('python').textContent=d.pythonConnected?'connected':'not connected';document.getElementById('python').className=d.pythonConnected?'ok':'bad';document.getElementById('status').innerHTML='ESP32 Wi-Fi: '+(d.wifiConnected?'connected':'disconnected')+'<br>ESP32 IP: '+d.ip+'<br>Python website: <span class="'+(d.pythonConnected?'ok':'bad')+'">'+(d.pythonConnected?'connected':'not connected')+'</span>'}async function move(which){await fetch('/api/'+which,{method:'POST'});refresh()}async function save(){await fetch('/api/save?open='+document.getElementById('open').value+'&closed='+document.getElementById('closed').value,{method:'POST'});refresh()}async function resetAngles(){await fetch('/api/reset',{method:'POST'});refresh()}refresh();setInterval(refresh,3000)</script></body></html>)HTML";
}

void handleCalibrationPage() {
  webServer.send(200, "text/html", calibrationPage());
}

void handleManualCalibrationPage() {
  String page = R"HTML(<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Lid calibration</title><style>body{margin:0;padding:20px;background:#eef3ef;color:#14251f;font:16px system-ui,sans-serif}main{max-width:540px;margin:auto;background:#fff;padding:22px;border-radius:18px;box-shadow:0 12px 35px #18352b22}label{display:block;margin:16px 0 6px;font-weight:700}.line{display:flex;gap:10px;align-items:center}.line input[type=number]{width:75px;padding:8px}.line input[type=range]{flex:1}button{border:0;border-radius:10px;padding:11px 14px;margin:5px;background:#173f34;color:#fff;font-weight:700}.secondary{background:#dcebe2;color:#173f34}.status{padding:12px;border-radius:10px;background:#f0f5f1;line-height:1.6}</style></head><body><main><h1>ESP32 Lid Calibration</h1><div class="status" id="status">Checking...</div><label>Open angle</label><div class="line"><input id="openNumber" type="number" min="0" max="180"><input id="openSlider" type="range" min="0" max="180"></div><label>Closed angle</label><div class="line"><input id="closedNumber" type="number" min="0" max="180"><input id="closedSlider" type="range" min="0" max="180"></div><p><button onclick="move('open')">Test open angle</button><button onclick="move('closed')">Test closed angle</button></p><p><button class="secondary" onclick="save()">Save angles</button><button class="secondary" onclick="resetAngles()">Reset defaults</button></p><p id="message"></p><script>let dirty=false;const $=id=>document.getElementById(id),clamp=v=>Math.max(0,Math.min(180,Number(v)||0));function sync(which,value){let n=$(which+'Number'),s=$(which+'Slider');value=clamp(value);n.value=value;s.value=value}function wire(which){$(which+'Number').oninput=()=>{dirty=true;sync(which,$(which+'Number').value)};$(which+'Slider').oninput=()=>{dirty=true;sync(which,$(which+'Slider').value)}}wire('open');wire('closed');async function refresh(){let d=await (await fetch('/api/status')).json();if(!dirty){sync('open',d.openAngle);sync('closed',d.closedAngle)}$('status').innerHTML='ESP32: '+(d.wifiConnected?'Wi-Fi connected':'Wi-Fi offline')+'<br>IP: '+d.ip+'<br>Python website: '+(d.pythonConnected?'connected':'not connected')}async function move(which){let angle=$(which+'Number').value;let r=await fetch('/api/move?angle='+angle,{method:'POST'});$('message').textContent=r.ok?'Moved to '+angle+' degrees':'Move failed'}async function save(){await fetch('/api/save?open='+$('openNumber').value+'&closed='+$('closedNumber').value,{method:'POST'});dirty=false;$('message').textContent='Angles saved';refresh()}async function resetAngles(){await fetch('/api/reset',{method:'POST'});dirty=false;refresh()}refresh();setInterval(refresh,3000)</script></main></body></html>)HTML";
  webServer.send(200, "text/html", page);
}

void handleCalibrationStatus() {
  String json = "{\"wifiConnected\":" + String(WiFi.status() == WL_CONNECTED ? "true" : "false") +
                ",\"pythonConnected\":" + String(pythonConnected ? "true" : "false") +
                ",\"ip\":\"" + (WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString() : "none") +
                "\",\"openAngle\":" + String(lidOpenAngle) +
                ",\"closedAngle\":" + String(lidClosedAngle) + "}";
  webServer.send(200, "application/json", json);
}

void handleCalibrationMove(bool closed) {
  webServer.send(200, "text/plain", closed ? "closed" : "open");
}

void handleCalibrationAngle() {
  if (!webServer.hasArg("angle")) {
    webServer.send(400, "text/plain", "Missing angle");
    return;
  }
  webServer.send(410, "text/plain", "Servo control has been removed; move the lid manually.");
}

void handleCalibrationSave() {
  if (!webServer.hasArg("open") || !webServer.hasArg("closed")) {
    webServer.send(400, "text/plain", "Missing angles");
    return;
  }
  lidOpenAngle = constrain(webServer.arg("open").toInt(), 0, 180);
  lidClosedAngle = constrain(webServer.arg("closed").toInt(), 0, 180);
  preferences.putInt("open", lidOpenAngle);
  preferences.putInt("closed", lidClosedAngle);
  webServer.send(200, "text/plain", "saved");
}

void handleCalibrationReset() {
  lidOpenAngle = DEFAULT_LID_OPEN_ANGLE;
  lidClosedAngle = DEFAULT_LID_CLOSED_ANGLE;
  preferences.putInt("open", lidOpenAngle);
  preferences.putInt("closed", lidClosedAngle);
  webServer.send(200, "text/plain", "reset");
}

void startCalibrationServer() {
  webServer.on("/", HTTP_GET, handleManualCalibrationPage);
  webServer.on("/api/status", HTTP_GET, handleCalibrationStatus);
  webServer.on("/api/open", HTTP_POST, []() { handleCalibrationMove(false); });
  webServer.on("/api/close", HTTP_POST, []() { handleCalibrationMove(true); });
  webServer.on("/api/move", HTTP_POST, handleCalibrationAngle);
  webServer.on("/api/save", HTTP_POST, handleCalibrationSave);
  webServer.on("/api/reset", HTTP_POST, handleCalibrationReset);
  webServer.begin();
}

bool postBillingAction(const char* path) {
  WiFiClient client;
  HTTPClient http;
  String url = String(BILLING_ACTION_BASE_URL) + path;
  http.setTimeout(2500);
  if (!http.begin(client, url)) return false;
  int responseCode = http.POST("");
  http.end();
  return responseCode >= 200 && responseCode < 300;
}

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  showMessage("Connecting WiFi", WIFI_SSID);
  unsigned long started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < 20000) {
    delay(300);
    Serial.print('.');
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("ESP32 IP: ");
    Serial.println(WiFi.localIP());
    showMessage("WiFi connected", WiFi.localIP().toString());
    delay(1200);
  } else {
    showMessage("WiFi failed", "Check settings");
  }
}

void displayBilling(JsonObject billing) {
  bool running = billing["running"] | false;
  float total = billing["total"] | 0.0f;
  JsonObject items = billing["items"].as<JsonObject>();
  if (!running) { showMessage("Billing offline", "Total: " + String(total, 2)); return; }
  if (millis() - lastScreenChange >= SCREEN_INTERVAL_MS) { screenIndex++; lastScreenChange = millis(); }
  if (screenIndex % 2 == 0 || items.isNull() || items.size() == 0) { showMessage("TOTAL", String(total, 2)); return; }
  int itemIndex = (screenIndex / 2) % max(1, static_cast<int>(items.size()));
  int currentIndex = 0;
  for (JsonPair item : items) {
    if (currentIndex++ == itemIndex) { showMessage(item.key().c_str(), "Qty: " + String(item.value().as<int>())); break; }
  }
}

void requestBillingStatus() {
  if (WiFi.status() != WL_CONNECTED) {
    pythonConnected = false;
    if (millis() - lastWiFiRetry >= WIFI_RETRY_INTERVAL_MS) { lastWiFiRetry = millis(); connectWiFi(); }
    return;
  }
  WiFiClient client;
  HTTPClient http;
  http.setTimeout(2500);
  if (!http.begin(client, BILLING_API_URL)) { pythonConnected = false; lastPythonError = "HTTP begin failed"; return; }
  int responseCode = http.GET();
  if (responseCode == HTTP_CODE_OK) {
    DynamicJsonDocument document(4096);
    DeserializationError error = deserializeJson(document, http.getString());
    if (error) { pythonConnected = false; lastPythonError = error.c_str(); showMessage("JSON error", "Check server"); }
    else { pythonConnected = true; lastPythonError = "Connected"; displayBilling(document["billing"].as<JsonObject>()); }
  } else { pythonConnected = false; lastPythonError = "HTTP " + String(responseCode); showMessage("Server offline", "HTTP " + String(responseCode)); }
  http.end();
}

void setup() {
  Serial.begin(115200);
  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  lcd.init();
  lcd.backlight();
  showMessage("Auto Billing", "ESP32 display");
  delay(1000);
  connectWiFi();
}

void loop() {
  if (millis() - lastStatusRequest >= STATUS_INTERVAL_MS) { lastStatusRequest = millis(); requestBillingStatus(); }
}
