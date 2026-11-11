import os
import io
import json
import base64
import subprocess
import tempfile
import shutil
import threading
import datetime
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

HOST = '0.0.0.0'
PORT = int(os.environ.get("PORT", 7860))

def get_ffmpeg_path():
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    return "ffmpeg"

FFMPEG_BIN = get_ffmpeg_path()

def process_and_upload(session_id, fps, webhook_url, player, frames):
    try:
        print(f"[*] Processing session: {session_id} | Frames: {len(frames)} | FPS: {fps}", flush=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            for idx, frame_b64 in enumerate(frames):
                img_bytes = base64.b64decode(frame_b64)
                file_path = os.path.join(tmpdir, f"frame_{idx:05d}.jpg")
                with open(file_path, "wb") as f:
                    f.write(img_bytes)

            output_mp4 = os.path.join(tmpdir, f"{session_id}.mp4")

            # تحويل الفيديو بدقة 640x480 بمعدل 24 FPS عالي السلاسة، مع سقف حجم أقصى مستحيل يتخطى 6-7 ميجا
            cmd = [
                FFMPEG_BIN, "-y",
                "-framerate", str(fps),
                "-i", os.path.join(tmpdir, "frame_%05d.jpg"),
                "-vf", "scale=640:480:flags=lanczos,eq=gamma=1.22:brightness=0.05:contrast=1.05,setsar=1",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                "-maxrate", "1600k",
                "-bufsize", "3200k",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                output_mp4
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res.returncode != 0:
                print(f"[!] FFmpeg error: {res.stderr}", flush=True)
                return

            file_size_kb = os.path.getsize(output_mp4) / 1024
            print(f"[*] MP4 created successfully: {file_size_kb:.1f} KB @ {fps} FPS", flush=True)

            if webhook_url and webhook_url.startswith("http"):
                with open(output_mp4, "rb") as video_file:
                    p_name = player.get('name', 'N/A')
                    p_account = player.get('account', 'N/A')
                    p_serial = player.get('serial', 'N/A')

                    description_text = (
                        f"The clip of player **{p_name}** was saved by the moderation team.\n\n"
                        f"👤 **Player details**\n"
                        f"```\n"
                        f"Name:    {p_name}\n"
                        f"Account: {p_account}\n"
                        f"Serial:  {p_serial}\n"
                        f"```"
                    )

                    file_name = f"{session_id}.mp4"

                    embed_payload = {
                        "username": "ULG RolePlay",
                        "embeds": [{
                            "title": "📹 Monitor Clip",
                            "description": description_text,
                            "color": 15822360,  # لون برتقالي أنيق مطابق للتصميم المطلوب
                            "image": {
                                "url": f"attachment://{file_name}"
                            },
                            "footer": {
                                "text": "ULG RolePlay"
                            },
                            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
                        }]
                    }

                    files = {
                        "payload_json": (None, json.dumps(embed_payload), "application/json"),
                        "files[0]": (file_name, video_file, "video/mp4")
                    }

                    discord_res = requests.post(webhook_url, files=files, timeout=45)
                    if discord_res.status_code in (200, 204):
                        print(f"[*] Discord upload successful: {discord_res.status_code}", flush=True)
                    else:
                        print(f"[!] Discord upload failed: {discord_res.status_code} - {discord_res.text}", flush=True)

    except Exception as e:
        print(f"[!] Exception during rendering: {e}", flush=True)

class VideoRenderHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Video Renderer Service</title>
            <style>
                body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
                .card { background: #1e293b; padding: 2.5rem 3rem; border-radius: 16px; box-shadow: 0 15px 35px rgba(0,0,0,0.5); text-align: center; border: 1px solid #334155; }
                .badge { display: inline-block; background: #10b981; color: white; padding: 0.35rem 1rem; border-radius: 9999px; font-weight: 700; font-size: 0.85rem; margin-bottom: 1.25rem; letter-spacing: 0.05em; }
                h1 { margin: 0 0 0.5rem 0; font-size: 1.5rem; color: #ffffff; }
                p { color: #94a3b8; margin: 0 0 1.25rem 0; font-size: 0.95rem; }
                code { background: #0f172a; color: #38bdf8; padding: 0.2rem 0.5rem; border-radius: 6px; font-family: monospace; }
            </style>
        </head>
        <body>
            <div class="card">
                <div class="badge">● ONLINE</div>
                <h1>MTA Video Rendering Service</h1>
                <p>Status: Ready to process replay clips.</p>
                <small style="color: #64748b;">API Endpoint: <code>/api/render-video</code> [POST]</small>
            </div>
        </body>
        </html>
        """
        self.wfile.write(html.strip().encode('utf-8'))

    def do_POST(self):
        if self.path == "/api/render-video":
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                post_data = self.rfile.read(content_length)
                
                raw_json = json.loads(post_data.decode('utf-8'))
                
                if isinstance(raw_json, list):
                    data = raw_json[0] if len(raw_json) > 0 else {}
                elif isinstance(raw_json, dict):
                    data = raw_json
                else:
                    data = {}

                session_id = data.get("sessionId", "clip")
                fps = int(data.get("fps", 20))
                if fps < 1:
                    fps = 20
                webhook_url = data.get("webhookUrl")
                raw_player = data.get("playerInfo", {})
                frames = data.get("frames", [])

                if isinstance(raw_player, dict):
                    player = raw_player
                elif isinstance(raw_player, list):
                    player = {
                        "name": raw_player[0] if len(raw_player) > 0 else "N/A",
                        "serial": raw_player[1] if len(raw_player) > 1 else "N/A",
                        "ip": raw_player[2] if len(raw_player) > 2 else "N/A"
                    }
                else:
                    player = {}

                if not frames:
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(b'{"error": "No frames provided"}')
                    return

                # الرد الفوري على سيرفر MTA بنجاح الاستلام لمنع أي مهلة زمنية (Timeout)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status": "accepted"}')

                # تشغيل المعالجة والرفع للديسكورد في الخلفية بشكل غير متزامن (Async Worker)
                worker = threading.Thread(
                    target=process_and_upload,
                    args=(session_id, fps, webhook_url, player, frames),
                    daemon=True
                )
                worker.start()

            except Exception as e:
                print(f"[!] Exception receiving payload: {e}", flush=True)
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

if __name__ == "__main__":
    print(f"[*] FFmpeg Binary: {FFMPEG_BIN}", flush=True)
    print(f"[*] Video Encoding Microservice running on http://{HOST}:{PORT}", flush=True)
    server = HTTPServer((HOST, PORT), VideoRenderHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Server stopped.", flush=True)
