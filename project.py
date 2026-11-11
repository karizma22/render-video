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
PUBLIC_BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://render-video-xyo1.onrender.com")

CLIPS_DIR = os.path.join(tempfile.gettempdir(), "mta_clips")
os.makedirs(CLIPS_DIR, exist_ok=True)

CLIPS_METADATA = {}

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

def serve_file(handler, filepath, content_type):
    if not os.path.isfile(filepath):
        handler.send_response(404)
        handler.end_headers()
        return

    file_size = os.path.getsize(filepath)
    range_header = handler.headers.get('Range')

    if range_header and range_header.startswith('bytes='):
        try:
            parts = range_header[6:].split('-')
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if parts[1] else file_size - 1
            if end >= file_size:
                end = file_size - 1
            content_length = end - start + 1

            handler.send_response(206)
            handler.send_header('Content-Type', content_type)
            handler.send_header('Content-Range', f'bytes {start}-{end}/{file_size}')
            handler.send_header('Content-Length', str(content_length))
            handler.send_header('Accept-Ranges', 'bytes')
            handler.end_headers()

            with open(filepath, 'rb') as f:
                f.seek(start)
                handler.wfile.write(f.read(content_length))
            return
        except Exception:
            pass

    handler.send_response(200)
    handler.send_header('Content-Type', content_type)
    handler.send_header('Content-Length', str(file_size))
    handler.send_header('Accept-Ranges', 'bytes')
    handler.end_headers()

    with open(filepath, 'rb') as f:
        shutil.copyfileobj(f, handler.wfile)

def serve_clip_page(handler, session_id):
    meta = CLIPS_METADATA.get(session_id, {})
    player = meta.get("player", {})
    p_name = player.get("name", "N/A")
    p_account = player.get("account", "N/A")
    p_serial = player.get("serial", "N/A")

    host_header = handler.headers.get("Host", "render-video-xyo1.onrender.com")
    scheme = "https" if ("render.com" in host_header or "https" in handler.headers.get("X-Forwarded-Proto", "")) else "http"
    base_url = f"{scheme}://{host_header}"

    video_url = f"{base_url}/videos/{session_id}.mp4"
    thumb_url = f"{base_url}/videos/{session_id}.jpg"

    description = (
        f"O clipe do jogador {p_name} foi salvo pela equipe de moderação.&#10;&#10;"
        f"👤 Detalhes do Player&#10;"
        f"Nome:    {p_name}&#10;"
        f"Account: {p_account}&#10;"
        f"Serial:  {p_serial}"
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>📹 Clipe de Monitoramento</title>
    <!-- OpenGraph Video Tags for Discord Video Embed -->
    <meta property="og:site_name" content="ULG RolePlay">
    <meta property="og:title" content="📹 Clipe de Monitoramento">
    <meta property="og:description" content="{description}">
    <meta property="theme-color" content="#f16f18">
    <meta property="og:type" content="video.other">
    <meta property="og:video" content="{video_url}">
    <meta property="og:video:secure_url" content="{video_url}">
    <meta property="og:video:type" content="video/mp4">
    <meta property="og:video:width" content="640">
    <meta property="og:video:height" content="480">
    <meta property="og:image" content="{thumb_url}">
    <meta property="og:image:width" content="640">
    <meta property="og:image:height" content="480">

    <meta name="twitter:card" content="player">
    <meta name="twitter:title" content="📹 Clipe de Monitoramento">
    <meta name="twitter:description" content="{description}">
    <meta name="twitter:player" content="{video_url}">
    <meta name="twitter:player:width" content="640">
    <meta name="twitter:player:height" content="480">
    <meta name="twitter:image" content="{thumb_url}">
</head>
<body style="background:#0f172a;color:#fff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
    <div style="text-align:center;">
        <h2 style="margin-bottom:1rem;">📹 ULG RolePlay - Clipe de Monitoramento</h2>
        <video controls autoplay width="640" height="480" style="max-width:92vw;border-radius:12px;box-shadow:0 15px 35px rgba(0,0,0,0.6);">
            <source src="/videos/{session_id}.mp4" type="video/mp4">
        </video>
    </div>
</body>
</html>"""

    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.end_headers()
    handler.wfile.write(html.encode("utf-8"))

def process_and_upload(session_id, fps, webhook_url, player, frames):
    try:
        print(f"[*] Processing session: {session_id} | Frames: {len(frames)} | FPS: {fps}", flush=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            first_frame_path = None
            for idx, frame_b64 in enumerate(frames):
                img_bytes = base64.b64decode(frame_b64)
                file_path = os.path.join(tmpdir, f"frame_{idx:05d}.jpg")
                with open(file_path, "wb") as f:
                    f.write(img_bytes)
                if idx == 0:
                    first_frame_path = file_path

            tmp_mp4 = os.path.join(tmpdir, f"{session_id}.mp4")

            # تحويل الفيديو بدقة 640x480 بمعدل 24 FPS عالي السلاسة
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
                tmp_mp4
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res.returncode != 0:
                print(f"[!] FFmpeg error: {res.stderr}", flush=True)
                return

            # حفظ الفيديو والبوستر في المجلد الثابت لاستضافتها
            final_mp4 = os.path.join(CLIPS_DIR, f"{session_id}.mp4")
            final_thumb = os.path.join(CLIPS_DIR, f"{session_id}.jpg")
            shutil.copyfile(tmp_mp4, final_mp4)
            if first_frame_path and os.path.isfile(first_frame_path):
                shutil.copyfile(first_frame_path, final_thumb)

            # حفظ تفاصيل اللاعب
            CLIPS_METADATA[session_id] = {
                "player": player,
                "fps": fps,
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }

            file_size_kb = os.path.getsize(final_mp4) / 1024
            print(f"[*] MP4 created and saved: {file_size_kb:.1f} KB @ {fps} FPS", flush=True)

            if webhook_url and webhook_url.startswith("http"):
                # استخدام رابط OpenGraph للحصول على الـ Embed المدمج مثل HexGuard
                public_host = os.environ.get("RENDER_EXTERNAL_URL") or PUBLIC_BASE_URL
                clip_url = f"{public_host}/clip/{session_id}"

                discord_payload = {
                    "username": "ULG RolePlay",
                    "content": clip_url
                }

                discord_res = requests.post(webhook_url, json=discord_payload, timeout=30)
                if discord_res.status_code in (200, 204):
                    print(f"[*] Discord OpenGraph Clip Link sent: {clip_url}", flush=True)
                else:
                    print(f"[!] Discord error: {discord_res.status_code} - {discord_res.text}", flush=True)

    except Exception as e:
        print(f"[!] Exception during rendering: {e}", flush=True)

class VideoRenderHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        clean_path = self.path.split("?")[0]

        if clean_path.startswith("/clip/"):
            session_id = clean_path.split("/clip/")[1].strip("/")
            serve_clip_page(self, session_id)
            return

        if clean_path.startswith("/videos/"):
            filename = os.path.basename(clean_path)
            filepath = os.path.join(CLIPS_DIR, filename)
            content_type = "video/mp4" if filename.endswith(".mp4") else "image/jpeg"
            serve_file(self, filepath, content_type)
            return

        # الصفحة الافتراضية لعرض حالة السيرفر
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
                body { font-family: system-ui, sans-serif; background: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
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
                fps = int(data.get("fps", 24))
                if fps < 1:
                    fps = 24
                webhook_url = data.get("webhookUrl")
                raw_player = data.get("playerInfo", {})
                frames = data.get("frames", [])

                if isinstance(raw_player, dict):
                    player = raw_player
                elif isinstance(raw_player, list):
                    player = {
                        "name": raw_player[0] if len(raw_player) > 0 else "N/A",
                        "account": raw_player[1] if len(raw_player) > 1 else "N/A",
                        "serial": raw_player[2] if len(raw_player) > 2 else "N/A"
                    }
                else:
                    player = {}

                if not frames:
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(b'{"error": "No frames provided"}')
                    return

                # الرد الفوري على سيرفر MTA بنجاح الاستلام
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status": "accepted"}')

                # تشغيل المعالجة والرفع للديسكورد في الخلفية بشكل غير متزامن
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
    print(f"[*] Public Base URL: {PUBLIC_BASE_URL}", flush=True)
    print(f"[*] Video Encoding Microservice running on http://{HOST}:{PORT}", flush=True)
    server = HTTPServer((HOST, PORT), VideoRenderHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Server stopped.", flush=True)
