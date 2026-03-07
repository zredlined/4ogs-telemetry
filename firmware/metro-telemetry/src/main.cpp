#include <Arduino.h>
#include <ArduinoJson.h>
#include <Adafruit_LSM6DSOX.h>
#include <Adafruit_NeoPixel.h>
#include <SparkFun_u-blox_GNSS_v3.h>
#include <cstdio>
#include <ctime>

// ── Config ──────────────────────────────────────────────────────────────────
static constexpr uint32_t PUBLISH_HZ = 25;
static constexpr uint32_t GNSS_NAV_HZ = 10;
static constexpr float METERS_PER_SEC_TO_MPH = 2.2369363f;
static constexpr float STANDARD_GRAVITY = 9.80665f;
static constexpr uint32_t SENSOR_RETRY_MS = 2000;

// ── Hardware ────────────────────────────────────────────────────────────────
Adafruit_LSM6DSOX lsm6dsox;
SFE_UBLOX_GNSS myGNSS;

#if defined(PIN_NEOPIXEL)
Adafruit_NeoPixel pixel(1, PIN_NEOPIXEL, NEO_GRB + NEO_KHZ800);
static constexpr bool HAS_NEOPIXEL = true;
#else
Adafruit_NeoPixel pixel;
static constexpr bool HAS_NEOPIXEL = false;
#endif

// ── State ───────────────────────────────────────────────────────────────────
uint32_t lastPublishMs = 0;
uint32_t lastSensorRetryMs = 0;
uint32_t sampleSeq = 0;
bool imuReady = false;
bool gnssReady = false;

// GPS-synced time: once we get a valid fix with date/time, we latch the
// relationship between millis() and Unix epoch so every sample gets a
// real-world timestamp — even between PVT updates.
bool timeSynced = false;
uint32_t syncMillis = 0;    // millis() at the moment we synced
double syncEpoch = 0.0;     // Unix epoch (seconds) at that moment

// ── Helpers ─────────────────────────────────────────────────────────────────
void setPixel(uint8_t r, uint8_t g, uint8_t b) {
  if (!HAS_NEOPIXEL) return;
  pixel.setPixelColor(0, pixel.Color(r, g, b));
  pixel.show();
}

const char* gearFromSpeed(float mph) {
  if (mph < 5.0f) return "N";
  if (mph < 24.0f) return "1";
  if (mph < 38.0f) return "2";
  if (mph < 56.0f) return "3";
  if (mph < 80.0f) return "4";
  if (mph < 108.0f) return "5";
  return "6";
}

int rpmEstimate(float mph, float throttle) {
  float rpm = 1300.0f + mph * 62.0f + throttle * 1100.0f;
  if (rpm < 900.0f) rpm = 900.0f;
  if (rpm > 9000.0f) rpm = 9000.0f;
  return static_cast<int>(rpm);
}

// Convert GPS date/time fields to Unix epoch seconds.
// GPS gives us UTC, so we use timegm-equivalent logic (no TZ offset).
double gpsToEpoch(int year, int month, int day, int hour, int min, int sec) {
  struct tm t = {};
  t.tm_year = year - 1900;
  t.tm_mon = month - 1;
  t.tm_mday = day;
  t.tm_hour = hour;
  t.tm_min = min;
  t.tm_sec = sec;
  // mktime interprets as local time; we want UTC.
  // ESP32 newlib defaults to UTC (no TZ set), so this is safe.
  time_t epoch = mktime(&t);
  return static_cast<double>(epoch);
}

// Return best-effort epoch timestamp for right now.
// If GPS time is synced, we extrapolate from the sync point using millis().
// Otherwise returns 0.0 (Pi side will substitute its own clock).
double currentEpoch(uint32_t now) {
  if (!timeSynced) return 0.0;
  uint32_t elapsed = now - syncMillis;
  return syncEpoch + static_cast<double>(elapsed) / 1000.0;
}

// ── I2C scan ────────────────────────────────────────────────────────────────
void scanI2C() {
  Serial.println("[diag] I2C scan start");
  uint8_t found = 0;
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.printf("[diag] I2C device @ 0x%02X\n", addr);
      found++;
    }
  }
  Serial.printf("[diag] I2C scan done, devices=%u\n", found);
}

// ── Sensor init ─────────────────────────────────────────────────────────────
void initOrRetrySensors(bool forceLog) {
  const uint32_t now = millis();
  if (!forceLog && (now - lastSensorRetryMs) < SENSOR_RETRY_MS) return;
  lastSensorRetryMs = now;

  if (!imuReady) {
    imuReady = lsm6dsox.begin_I2C();
    Serial.printf("[diag] LSM6DSOX %s\n", imuReady ? "OK" : "MISSING");
    if (imuReady) {
      lsm6dsox.setAccelRange(LSM6DS_ACCEL_RANGE_16_G);
      lsm6dsox.setGyroRange(LSM6DS_GYRO_RANGE_2000_DPS);
      lsm6dsox.setAccelDataRate(LSM6DS_RATE_208_HZ);
      lsm6dsox.setGyroDataRate(LSM6DS_RATE_208_HZ);
    }
  }

  if (!gnssReady) {
    gnssReady = myGNSS.begin(Wire);
    Serial.printf("[diag] u-blox GNSS %s\n", gnssReady ? "OK" : "MISSING");
    if (gnssReady) {
      myGNSS.setI2COutput(COM_TYPE_UBX);
      bool navOk = myGNSS.setNavigationFrequency(GNSS_NAV_HZ);
      Serial.printf("[diag] GNSS nav rate %dHz %s\n", GNSS_NAV_HZ, navOk ? "OK" : "FAIL");
      myGNSS.setAutoPVT(true);
    }
  }
}

// ── Setup ───────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  uint32_t serialStart = millis();
  while (!Serial && (millis() - serialStart) < 3000) {
    delay(10);
  }
  delay(200);

  Serial.println("\n========================================");
  Serial.println("  4OGS Metro Telemetry (USB serial)");
  Serial.printf("  FW build %s %s\n", __DATE__, __TIME__);
  Serial.println("========================================");

#if defined(NEOPIXEL_POWER)
  pinMode(NEOPIXEL_POWER, OUTPUT);
  digitalWrite(NEOPIXEL_POWER, HIGH);
  delay(10);
#endif

#if defined(PIN_NEOPIXEL)
  pixel.begin();
  pixel.setBrightness(40);
  setPixel(0, 0, 20);
#endif

#ifdef LED_BUILTIN
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, LOW);
#endif

  Wire.begin();
  Wire.setClock(400000);
  delay(50);
  scanI2C();
  initOrRetrySensors(true);

  Serial.println("[diag] setup complete, entering loop");
}

// ── Loop ────────────────────────────────────────────────────────────────────
void loop() {
  initOrRetrySensors(false);

  const uint32_t now = millis();
  const uint32_t publishPeriodMs = 1000 / PUBLISH_HZ;

  if (now - lastPublishMs < publishPeriodMs) {
    delay(1);
    return;
  }
  lastPublishMs = now;
  sampleSeq++;

  // ── Read IMU ──
  float g_lat = 0.0f, g_long = 0.0f;
  if (imuReady) {
    sensors_event_t accel, gyro, temp;
    lsm6dsox.getEvent(&accel, &gyro, &temp);
    g_long = accel.acceleration.x / STANDARD_GRAVITY;
    g_lat = accel.acceleration.y / STANDARD_GRAVITY;
  }

  // ── Read GNSS (non-blocking with autoPVT) ──
  float speed_mph = 0.0f;
  bool gps_fix = false;
  int gps_sats = 0;
  float gps_hdop = 99.9f;
  float gps_lat_deg = 0.0f, gps_lon_deg = 0.0f, gps_alt_m = 0.0f;
  char gps_time_utc[24] = "--";

  if (gnssReady && myGNSS.getPVT()) {
    float speedMps = static_cast<float>(myGNSS.getGroundSpeed()) / 1000.0f;
    speed_mph = speedMps * METERS_PER_SEC_TO_MPH;
    gps_sats = static_cast<int>(myGNSS.getSIV());
    gps_hdop = static_cast<float>(myGNSS.getHorizontalDOP()) / 100.0f;
    gps_lat_deg = static_cast<float>(myGNSS.getLatitude()) / 10000000.0f;
    gps_lon_deg = static_cast<float>(myGNSS.getLongitude()) / 10000000.0f;
    gps_alt_m = static_cast<float>(myGNSS.getAltitude()) / 1000.0f;
    uint8_t fixType = myGNSS.getFixType();
    gps_fix = (fixType >= 3) && (gps_sats >= 4);

    int yr = (int)myGNSS.getYear();
    int mo = (int)myGNSS.getMonth();
    int dy = (int)myGNSS.getDay();
    int hr = (int)myGNSS.getHour();
    int mn = (int)myGNSS.getMinute();
    int sc = (int)myGNSS.getSecond();

    if (yr >= 2024 && mo >= 1 && mo <= 12 && dy >= 1 && dy <= 31) {
      snprintf(gps_time_utc, sizeof(gps_time_utc),
               "%04d-%02d-%02dT%02d:%02d:%02dZ", yr, mo, dy, hr, mn, sc);
    } else {
      snprintf(gps_time_utc, sizeof(gps_time_utc), "--");
    }

    // Re-sync our clock on valid fix + plausible date
    if (gps_fix && yr >= 2024) {
      syncEpoch = gpsToEpoch(yr, mo, dy, hr, mn, sc);
      syncMillis = now;
      if (!timeSynced) {
        timeSynced = true;
        Serial.printf("[diag] GPS time synced: %s (epoch=%.0f)\n", gps_time_utc, syncEpoch);
      }
    }
  }

  float throttle = constrain(speed_mph / 110.0f, 0.0f, 1.0f);
  const char* gear = gearFromSpeed(speed_mph);
  int rpm = rpmEstimate(speed_mph, throttle);

  // ── Serialize and send as a single JSON line over USB serial ──
  JsonDocument doc;
  doc["seq"] = sampleSeq;
  doc["ms"] = now;

  double epoch = currentEpoch(now);
  if (epoch > 0.0) {
    doc["t"] = serialized(String(epoch, 3));
  }

  doc["speed_mph"] = speed_mph;
  doc["rpm"] = rpm;
  doc["gear"] = gear;
  doc["g_lat"] = g_lat;
  doc["g_long"] = g_long;
  doc["throttle"] = throttle;
  doc["brake"] = 0.0f;

  JsonObject gps = doc["gps"].to<JsonObject>();
  gps["fix"] = gps_fix;
  gps["sats"] = gps_sats;
  gps["hdop"] = gps_hdop;
  gps["lat"] = gps_lat_deg;
  gps["lon"] = gps_lon_deg;
  gps["alt_m"] = gps_alt_m;
  gps["time_utc"] = gps_time_utc;

  serializeJson(doc, Serial);
  Serial.println();

  // ── Status LED ──
  if (gps_fix) {
    setPixel(0, 30, 0);       // green = GPS fix
  } else if (gnssReady) {
    setPixel(0, 5, 24);       // blue = searching for sats
  } else if (imuReady) {
    setPixel(25, 18, 0);      // amber = IMU only
  } else {
    setPixel(25, 0, 0);       // red = no sensors
  }

#ifdef LED_BUILTIN
  digitalWrite(LED_BUILTIN, (sampleSeq & 1) ? HIGH : LOW);
#endif
}
