#include <Arduino.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <mbedtls/md.h>
#include <ArduinoJson.h>

#define WIFI_SSID     "Himanshu's iPhone"
#define WIFI_PASSWORD "Himanshu."
#define ONE_WIRE_BUS  4
#define SOIL_PIN      34
#define MQTT_BROKER   "broker.hivemq.com"
#define MQTT_PORT     1883
#define MQTT_TOPIC    "agri/sensor/data"
#define MQTT_HB_TOPIC "agri/sensor/heartbeat"
#define DEVICE_ID     "AGRI_001"
#define HMAC_SECRET   "da92adc6c5e653a9e87b9717a781a230eaa976debffd0127f3e583c43b535bbf"

OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature sensors(&oneWire);
WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);
int readingCount = 0;

void connectWiFi() {
  Serial.print("Connecting to WiFi");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 20) {
    delay(500); Serial.print("."); tries++;
  }
  if (WiFi.status() == WL_CONNECTED)
    Serial.println("\nWiFi connected: " + WiFi.localIP().toString());
  else
    Serial.println("\nWiFi FAILED");
}

void connectMQTT() {
  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  int tries = 0;
  while (!mqtt.connected() && tries < 5) {
    Serial.print("Connecting MQTT...");
    String clientId = "ESP32_" + String(random(0xffff), HEX);
    if (mqtt.connect(clientId.c_str())) {
      Serial.println("connected");
    } else {
      Serial.print("failed rc="); Serial.println(mqtt.state());
      delay(2000); tries++;
    }
  }
}

String computeHMAC(String payload) {
  byte hmacResult[32];
  mbedtls_md_context_t ctx;
  mbedtls_md_init(&ctx);
  mbedtls_md_setup(&ctx, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), 1);
  mbedtls_md_hmac_starts(&ctx, (const unsigned char*)HMAC_SECRET, strlen(HMAC_SECRET));
  mbedtls_md_hmac_update(&ctx, (const unsigned char*)payload.c_str(), payload.length());
  mbedtls_md_hmac_finish(&ctx, hmacResult);
  mbedtls_md_free(&ctx);
  String hex = "";
  for (int i = 0; i < 32; i++) {
    if (hmacResult[i] < 16) hex += "0";
    hex += String(hmacResult[i], HEX);
  }
  return hex;
}

int readSoilRaw() {
  long sum = 0;
  for (int i = 0; i < 10; i++) { sum += analogRead(SOIL_PIN); delay(10); }
  return sum / 10;
}

float readTemp() {
  sensors.requestTemperatures();
  float t = sensors.getTempCByIndex(0);
  if (t == DEVICE_DISCONNECTED_C || t == 85.0 || t == -127.0) return NAN;
  return t;
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  sensors.begin();
  analogReadResolution(12);
  Serial.println("System Initialized...");
  connectWiFi();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWiFi();
  if (!mqtt.connected()) connectMQTT();
  mqtt.loop();

  float tempC = readTemp();
  int soilRaw = readSoilRaw();
  int soilPct = constrain(map(soilRaw, 3200, 1500, 0, 100), 0, 100);
  readingCount++;

  Serial.println("-----------------------------");
  if (isnan(tempC)) { Serial.println("TEMP: ERROR"); delay(5000); return; }
  Serial.print("TEMP: "); Serial.print(tempC); Serial.println(" C");
  Serial.print("SOIL: "); Serial.print(soilPct); Serial.println("%");
  Serial.print("RAW:  "); Serial.println(soilRaw);

  String nonce = String(millis()) + String(random(0xffff), HEX);

  StaticJsonDocument<512> doc;
  doc["device_id"]     = DEVICE_ID;
  doc["temperature"]   = round(tempC * 100.0) / 100.0;
  doc["soil_percent"]  = soilPct;
  doc["soil_raw"]      = soilRaw;
  doc["temp_valid"]    = true;
  doc["soil_valid"]    = true;
  doc["nonce"]         = nonce;
  doc["timestamp"]     = (long)millis();
  doc["reading_count"] = readingCount;

  String payload;
  serializeJson(doc, payload);
  String hmac = computeHMAC(payload);

  StaticJsonDocument<600> outer;
  outer["payload"] = doc;
  outer["hmac"]    = hmac;

  String finalMsg;
  serializeJson(outer, finalMsg);

  if (mqtt.publish(MQTT_TOPIC, finalMsg.c_str()))
    Serial.println("Published to MQTT ✓");
  else
    Serial.println("MQTT publish FAILED");

  if (readingCount % 6 == 0) {
    StaticJsonDocument<200> hb;