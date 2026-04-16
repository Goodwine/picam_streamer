import argparse
import time
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

class CameraSource:
    def __init__(self, args, on_frame):
        self.args = args
        self.on_frame = on_frame
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        self._initialize()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)

    def _initialize(self):
        pass

    def _run(self):
        raise NotImplementedError

class PiCameraSource(CameraSource):
    def _initialize(self):
        from picamera2 import Picamera2
        from libcamera import Transform

        self.cam = Picamera2()
        transform = Transform(
            hflip=bool(self.args.fliph),
            vflip=bool(self.args.flipv)
        )
        self.cam.configure(self.cam.create_video_configuration(
            main={"size": (self.args.width, self.args.height)}, transform=transform
        ))

    def _run(self):
        from picamera2.encoders import JpegEncoder
        from picamera2.outputs import FileOutput
        import io

        class FrameOutput(io.BufferedIOBase):
            def write(buf_self, buf):
                self.on_frame(bytes(buf))
                return len(buf)

        self.cam.start_recording(JpegEncoder(), FileOutput(FrameOutput()))
        self.stop_event.wait()
        self.cam.stop_recording()
        self.cam.close()

class WebcamSource(CameraSource):
    def _initialize(self):
        import cv2
        self.cv2 = cv2
        self.cap = cv2.VideoCapture(0)
        if not self.cap.isOpened():
            raise RuntimeError("Could not open webcam.")
            
        # Request hardware MJPEG stream to prevent USB/IP packet loss
        self.cap.set(cv2.CAP_PROP_FOURCC, self.cv2.VideoWriter_fourcc(*'MJPG'))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.args.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.args.height)

    def _run(self):
        flip_code = -1 if self.args.fliph and self.args.flipv else \
                     1 if self.args.fliph else \
                     0 if self.args.flipv else None

        while not self.stop_event.is_set():
            ret, frame = self.cap.read()
            if not ret:
                time.sleep(0.1)
                continue
            if flip_code is not None:
                frame = self.cv2.flip(frame, flip_code)
            ret, buf = self.cv2.imencode('.jpg', frame)
            if ret:
                self.on_frame(buf.tobytes())
        self.cap.release()

class CameraManager:
    def __init__(self, args):
        self.args = args
        self.viewers = 0
        self.frame = None
        self.cam = None
        self.idle_timer = None
        self.shutting_down = False
        self.lock = threading.Condition()

    def _on_frame(self, frame):
        with self.lock:
            self.frame = frame
            self.lock.notify_all()

    def get_frame(self):
        with self.lock:
            self.lock.wait()
            return self.frame

    def acquire(self):
        with self.lock:
            self.viewers += 1
            if self.idle_timer:
                self.idle_timer.cancel()
                self.idle_timer = None
            if not self.cam:
                self._start_hardware()

    def release(self):
        with self.lock:
            self.viewers = max(0, self.viewers - 1)
            if self.viewers == 0 and not self.shutting_down:
                if self.args.timeout == 0:
                    self._stop_hardware()
                elif self.args.timeout > 0:
                    logging.info(f"No viewers. Stopping in {self.args.timeout}s.")
                    self.idle_timer = threading.Timer(self.args.timeout, self._stop_hardware)
                    # Otherwise, this prevents the program from exiting until the timer runs out.
                    self.idle_timer.daemon = True
                    self.idle_timer.start()

    def _start_hardware(self):
        source_map = {
            'picamera': (PiCameraSource, 'picamera2'),
            'webcam': (WebcamSource, 'opencv-python')
        }

        for name in self.args.source:
            SrcClass, lib = source_map[name]
            try:
                self.cam = SrcClass(self.args, self._on_frame)
                self.cam.start()
                logging.info(f"Started camera: {name}")
                return
            except ImportError:
                logging.warning(f"Could not start {name}: relies on missing '{lib}' library")
            except Exception as e:
                logging.warning(f"Failed to start {name}: {e}")
                self.cam = None

        logging.error("No cameras available.")

    def _stop_hardware(self, reason="idle"):
        with self.lock:
            if self.cam:
                logging.info(f"Camera turned off ({reason}).")
                self.cam.stop()
                self.cam = None
                self.frame = None

class StreamHandler(BaseHTTPRequestHandler):
    """
    Handles incoming HTTP GET requests from viewers.
    
    This continuously pushes a multipart stream using the data generated
    by the CameraManager. It relies on the HTTP standard multipart/x-mixed-replace
    type, which tells the browser to keep replacing the image in real-time.
    """
    def do_GET(self):
        manager = self.server.camera_manager
        manager.acquire()
        
        if not manager.cam:
            self.send_error(500, "Camera hardware failed to start or is currently unavailable.")
            return

        self.send_response(200)
        self.send_header('Age', '0')
        self.send_header('Cache-Control', 'no-cache, private')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
        self.end_headers()

        try:
            while True:
                frame = manager.get_frame()
                # frame becomes falsy when the camera hardware shuts down
                if frame:
                    self.wfile.write(b'--FRAME\r\n')
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', len(frame))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b'\r\n')
        except Exception:
            pass
        finally:
            manager.release()

def main():
    p = argparse.ArgumentParser(
        description="MJPEG streaming server for Raspberry Pi or Webcam",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    p.add_argument('--host', default='127.0.0.1', help='Host IP to bind the server to')
    p.add_argument('--port', type=int, default=8080, help='Port to bind the server to')
    p.add_argument('--source', nargs='+', choices=['picamera', 'webcam'], default=['picamera', 'webcam'], help='Ordered list of preferred camera sources to attempt')
    p.add_argument('--width', type=int, default=640, help='Camera stream resolution width')
    p.add_argument('--height', type=int, default=480, help='Camera stream resolution height')
    p.add_argument('--fliph', action='store_true', help='Flip the camera stream horizontally')
    p.add_argument('--flipv', action='store_true', help='Flip the camera stream vertically')
    p.add_argument('--timeout', type=float, default=5, help='Seconds to wait while idle before shutting down camera. Negative values never shut down.')

    args = p.parse_args()

    manager = CameraManager(args)
    # A negative timeout indicates the camera should never automatically shut down,
    # so we manually force-start the hardware immediately upon launching.
    if args.timeout < 0:
        manager.acquire()

    server = HTTPServer((args.host, args.port), StreamHandler)
    server.camera_manager = manager

    logging.info(f"Serving at http://{args.host}:{args.port}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        manager.shutting_down = True
        logging.info("Keyboard interrupt received, shutting down...")
    finally:
        server.server_close()
        # Force a synchronous stop hook to prevent camera hardware lockups 
        # that could persist on the daemon thread while python exits.
        manager._stop_hardware(reason="interrupt")

if __name__ == '__main__':
    main()
