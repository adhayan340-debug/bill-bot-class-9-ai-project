# ESP32 LCD Billing Controller

This firmware is for a regular ESP32 DevKit plus a 16x2 I2C LCD. It has no keypad code. It connects to the laptop hotspot, polls the Flask billing API, and displays the billing total and item quantities.

## Wiring

| LCD I2C | ESP32 |
|---|---|
| VCC | 5V/VIN |
| GND | GND |
| SDA | GPIO 21 |
| SCL | GPIO 22 |

The code assumes LCD address `0x27`. If the LCD stays blank but the backlight is on, change `LCD_ADDRESS` in `src/main.cpp` to `0x3F`.

## Configure

Edit these lines in `src/main.cpp`:

```cpp
const char* WIFI_SSID = "YOUR_HOTSPOT_NAME";
const char* WIFI_PASSWORD = "YOUR_HOTSPOT_PASSWORD";
const char* BILLING_API_URL = "http://192.168.137.1:5000/api/stats";
```

Use the laptop hotspot adapter IP in `BILLING_API_URL`. Windows commonly uses `192.168.137.1`, but confirm with `ipconfig`.

## Upload with PlatformIO

1. Install the VS Code extension **PlatformIO IDE**.
2. Open the `esp32_controller` folder in VS Code.
3. Connect the ESP32 with a USB data cable.
4. Select the correct COM port if PlatformIO asks.
5. Click PlatformIO's Upload arrow.
6. Open Serial Monitor at `115200` baud.

PlatformIO automatically installs the libraries from `platformio.ini`:

- `LiquidCrystal_I2C`
- `ArduinoJson`
- ESP32 Wi-Fi and HTTP libraries are included with the Arduino ESP32 framework.

## Run order

1. Turn on the laptop hotspot.
2. Connect the ESP32 to the hotspot.
3. Start Flask on the laptop:

```powershell
python app.py --host 0.0.0.0 --port 5000
```

4. Start billing mode from the Flask website.
5. The LCD will show `Billing offline` until billing is started, then show the total and detected quantities.
