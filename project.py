import os
import io
import json
import base64
import subprocess
import tempfile
import shutil
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

class VideoRenderHandler(BaseHTTPRequestHandler):
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
                fps = int(data.get("fps", 15))
                if fps < 1:
                    fps = 15
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
                    print(f"[!] Warning: No frames received for session {session_id}", flush=True)
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(b'{"error": "No frames provided"}')
                    return

                print(f"[*] Processing session: {session_id} | Frames: {len(frames)} | FPS: {fps}", flush=True)

                with tempfile.TemporaryDirectory() as tmpdir:
                    for idx, frame_b64 in enumerate(frames):
                        img_bytes = base64.b64decode(frame_b64)
                        file_path = os.path.join(tmpdir, f"frame_{idx:05d}.jpg")
                        with open(file_path, "wb") as f:
                            f.write(img_bytes)

                    output_mp4 = os.path.join(tmpdir, f"{session_id}.mp4")

                    # تحويل الفيديو إلى دقة 680x480 مدمجة بدون هوامش سوداء وفلتر lanczos للنقاء
                    cmd = [
                        FFMPEG_BIN, "-y",
                        "-framerate", str(fps),
                        "-i", os.path.join(tmpdir, "frame_%05d.jpg"),

                        "-vf",
                        "scale=680:480:flags=lanczos,eq=gamma=1.25:brightness=0.06:contrast=1.05,setsar=1",

                        "-c:v", "libx264",
                        "-preset", "fast",
                        "-crf", "16",
                        "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart",

                        output_mp4
                    ]
                    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    if res.returncode != 0:
                        print(f"[!] FFmpeg error: {res.stderr}", flush=True)
                        self.send_response(500)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(b'{"error": "FFmpeg encoding failed"}')
                        return

                    file_size_kb = os.path.getsize(output_mp4) / 1024
                    print(f"[*] MP4 created successfully: {file_size_kb:.1f} KB @ {fps} FPS", flush=True)

                    if webhook_url and webhook_url.startswith("http"):
                        with open(output_mp4, "rb") as video_file:
                            duration_sec = round(len(frames) / fps, 1)
                            embed_payload = {
                                "username": "AntiCheat Screen Recorder",
                                "embeds": [{
                                    "title": f"🎥 ريبلاي شاشة المشتبه به (آخر {duration_sec} ثانية)",
                                    "color": 15158332,
                                    "fields": [
                                        {"name": "Player Name", "value": f"```{player.get('name', 'N/A')}```", "inline": True},
                                        {"name": "IP Address", "value": f"```{player.get('ip', 'N/A')}```", "inline": True},
                                        {"name": "Serial", "value": f"```{player.get('serial', 'N/A')}```", "inline": False},
                                        {"name": "Clip Duration", "value": f"```{duration_sec}s```", "inline": True},
                                        {"name": "Frames Captured", "value": f"```{len(frames)} frames @ {fps} FPS```", "inline": True},
                                        {"name": "Resolution", "value": "```680x480```", "inline": True}
                                    ],
                                    "footer": {"text": "Chicago Roleplay Anti-Cheat"}
                                }]
                            }
    
                            files = {
                                "file": (f"{session_id}.mp4", video_file, "video/mp4"),
                                "payload_json": (None, json.dumps(embed_payload), "application/json")
                            }

                            discord_res = requests.post(webhook_url, files=files, timeout=45)
                            if discord_res.status_code in (200, 204):
                                print(f"[*] Discord upload successful: {discord_res.status_code}", flush=True)
                            else:
                                print(f"[!] Discord upload failed: {discord_res.status_code} - {discord_res.text}", flush=True)

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')

            except Exception as e:
                print(f"[!] Exception during rendering: {e}", flush=True)
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
