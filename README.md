# VisiCut Pi Camera Streamer

A lightweight Python script that streams a Raspberry Pi camera (or webcam fallback) into an MJPEG
HTTP stream, intended for use with [VisiCut](https://github.com/t-oster/VisiCut).

The camera only starts when a client is connected, disconnecting when all clients disconnect (with a
configurable timeout).

## Setup

The script is meant for a Raspberry Pi. It depends on `picamera2` to access the camera module and
`opencv-python` for webcam fallback. The script can gracefully shut down with `Ctrl+C`.

```bash
sudo apt update
sudo apt install python3-picamera2 python3-opencv
```

Clone the code and test the internal flags:

```bash
python3 server.py --help
```

### Usage Flags

- `--host` / `--port`: Defaults to `127.0.0.1:8080`.
- `--source`: Ordered list of camera sources to attempt. Defaults to `picamera webcam`.
- `--width` / `--height`: Camera stream output scale
- `--fliph` / `--flipv`: Flips the camera image horizontally and/or vertically.
- `--timeout`: Seconds to wait before stopping the camera when there are no viewers. Defaults to
  `5`. A negative value (e.g. `-1`) keeps the idle camera running indefinitely.
- `--fps`: Target camera framerate (frames per second). Defaults to `1`.
- `--debug`: Enable debug level logging. This logs the requester's IP address.

## Endpoints

- **Continuous Stream**: Any path except the snapshot ones (e.g., `http://<host>:<port>/`) returns a
  standard multipart MJPEG stream.
- **Single Snapshot**: `/snapshot` or `/snapshot.jpg` returns a single static JPEG frame.

## VisiCut Configuration

VisiCut expects a single image rather than a continuous MJPEG stream. Use the snapshot endpoint for
the camera URL:

`http://<your_host>:<your_port>/snapshot`

### Recommended Settings

In the VisiCut Camera Settings:

- **URL**: `http://<your_host>:<your_port>/snapshot`
- **Timings (ms)**: This field controls the polling rate. It is recommended to set this between
  **1000 and 3000** (1 to 3 seconds) to ensure a stable feed without overwhelming the network.

### Examples

Serve the stream on all interfaces on port 1234:

```bash
python3 server.py --host 0.0.0.0 --port 1234
```

Flip the lens completely and keep the camera running indefinitely, even when idle:

```bash
python3 server.py --fliph --flipv --timeout -1
```
