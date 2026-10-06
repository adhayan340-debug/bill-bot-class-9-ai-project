# ESP32-CAM Auto Billing

The project is split into three separate stages: capture images, train a detector, and run billing.

## Setup

1. Install Python 3.10 or newer.
2. Open PowerShell in this folder.
3. Create and activate a virtual environment:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

4. Flash the ESP32-CAM `CameraWebServer` example and find its IP address.

## 1. Capture images

Run:

```powershell
python capture_images.py --stream http://ESP32_IP:81/stream
```

Press `s` for each useful image and `q` to quit. Images are saved in `dataset/raw`.

Capture each product alone and in combinations, using different positions, distances, and lighting.

## 2. Annotate and train

The raw images must be annotated with bounding boxes before training. Roboflow, CVAT, or Label Studio can export the YOLO format.

The final dataset should have this structure:

```text
dataset/
	data.yaml
	images/train/
	images/val/
	labels/train/
	labels/val/
```

The `data.yaml` class names must exactly match the product names in `prices.json`. Then run:

```powershell
python train_model.py --data dataset/data.yaml --epochs 50
```

Copy the best weights from `runs/detect/product_detector/weights/best.pt` to this project folder as `best.pt`.

## 3. Run billing

Change product prices in `prices.json`, then run:

```powershell
python billing.py --stream http://ESP32_IP:81/stream --model best.pt
```

The default stream URL is `http://192.168.4.1:81/stream`.

## Controls

- `s`: save the current annotated frame
- `c`: clear the current bill and start a new billing session
- `b`: save a text receipt
- `q`: quit

The billing program labels objects on every frame, but counts each tracking ID only once. If an object leaves the camera view and later comes back, it may receive a new ID and can be billed again. For reliable checkout, place products in a fixed tray and press `c` before each customer.

## ESP32-CAM stream

Flash the Arduino IDE example `File > Examples > ESP32 > Camera > CameraWebServer`, select the `AI Thinker ESP32-CAM` board, enter Wi-Fi credentials, and print the camera IP address. Use that address in the command above.

The laptop and ESP32-CAM must be on the same Wi-Fi network. For an initial test, open `http://ESP32_IP` in a browser; the camera page should load before starting this program.
 
The optional ESP32 LCD controller has separate firmware in `main.cpp` and `arduino_esp32_controller/`. Replace its Wi-Fi and laptop API address placeholders locally before flashing. Keep real network credentials out of public commits.

## Web dashboard

The Flask dashboard puts capture, training, prices, and billing in one browser interface. Start it on the laptop with:

```powershell
python app.py --host 0.0.0.0 --port 5000
```

On the laptop, open `http://127.0.0.1:5000`. On another device connected to the same network, open `http://LAPTOP_IP:5000`, replacing `LAPTOP_IP` with the laptop's local IPv4 address from `ipconfig`.

The dashboard starts the camera reader automatically, captures images into `dataset/raw`, runs training in the background, and runs billing in the browser. Windows Firewall may ask permission for Python; allow private-network access so other devices can connect.

The dashboard pages are available at `/billing`, `/capture`, `/train`, and `/prices`. If `best.pt` is not available yet, Capture + AI label automatically uses `yolo11n.pt` for general object labels. After you train your own product model, it will use `best.pt` instead.

## Admin protection

Camera and billing are public at `/` and `/billing`. Training and price management require `/admin/login`.

Admin access can use the laptop-only local account or Google OAuth. Configure `ADHAYAN_ADMIN_PASSWORD` in `.env` to enable the local account for `Adhayan Das`; local login is disabled when this variable is unset. Add more local accounts in the `ADMIN_USERS` section in `app.py`. Local admin pages reject network devices and accept only `127.0.0.1`/`::1`.

Google OAuth remains available on the laptop when configured. Copy `.env.example` to `.env`, fill in `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, and restart Flask. Create a Google OAuth web client and add `http://127.0.0.1:5000/auth/google/callback` as an authorized redirect URI. Only the verified Google account `adhayan340@gmail.com` is accepted. You can override it with `GOOGLE_ALLOWED_EMAIL`.

