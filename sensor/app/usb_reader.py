import sys
import time
from typing import Optional

try:
    import usb.core
    import usb.util
except ImportError:
    usb = None  # type: ignore

try:
    import serial as pyserial
except ImportError:
    pyserial = None  # type: ignore

# HID device (WS1361 / standard HY1361)
HID_VENDOR_ID = 0x16C0
HID_PRODUCT_ID = 0x05DC

# Serial device (CH340 variant, VID=0x1A86 PID=0x7523)
# Protocol: 6-byte framed packets at 115200 baud
#   0x55  [b_high]  [b_low]  0x01  0x01  0xAA  (every 500ms)
#   dB = int.from_bytes([b_high, b_low], "big") / 10.0
SERIAL_VENDOR_ID = 0x1A86
SERIAL_PRODUCT_ID = 0x7523
SERIAL_PORT = "/dev/ttyUSB0"
SERIAL_BAUD = 115200
_SERIAL_START = 0x55
_SERIAL_END = 0xAA
_SERIAL_PKT_LEN = 6

SPL_MIN = 30.0
SPL_MAX = 130.0


def _decode_spl(b0: int, b1: int) -> float:
    """b0 = b_high (data[1]), b1 = b_low (data[2])."""
    return round(int.from_bytes([b0, b1], "big") / 10.0, 1)


# ── HID device (pyusb control-transfer) ───────────────────────────────────────


class _HidDevice:
    def __init__(self, dev):
        self._dev = dev

    def read(self, logger) -> Optional[float]:
        try:
            data = self._dev.ctrl_transfer(0xC0, 4, 0, 0, 200)
            return _decode_spl(data[0], data[1])
        except Exception as exc:
            logger.warning(f"USB HID read failed: {exc}")
            time.sleep(0.1)
            return None

    def close(self):
        pass


# ── Serial device (CH340 / pyserial) ──────────────────────────────────────────


class _SerialDevice:
    def __init__(self, port: str, baud: int):
        self._ser = pyserial.Serial(port, baud, timeout=0.05)
        self._ser.reset_input_buffer()
        self._buf = bytearray()

    def read(self, logger) -> Optional[float]:
        try:
            self._buf += self._ser.read(32)
            # Find and consume complete 6-byte framed packets: 0x55 hi lo 0x01 0x01 0xAA
            while True:
                start = self._buf.find(_SERIAL_START)
                if start == -1:
                    self._buf.clear()
                    break
                if start > 0:
                    self._buf = self._buf[start:]  # discard leading garbage
                if len(self._buf) < _SERIAL_PKT_LEN:
                    break
                if self._buf[_SERIAL_PKT_LEN - 1] == _SERIAL_END:
                    pkt = self._buf[:_SERIAL_PKT_LEN]
                    self._buf = self._buf[_SERIAL_PKT_LEN:]
                    b_high, b_low = pkt[1], pkt[2]
                    val = _decode_spl(b_high, b_low)
                    if SPL_MIN <= val <= SPL_MAX:
                        return val
                else:
                    self._buf = self._buf[1:]  # bad packet — re-sync
            return None
        except Exception as exc:
            logger.warning(f"Serial read failed: {exc}")
            time.sleep(0.1)
            return None

    def close(self):
        try:
            self._ser.close()
        except Exception:
            pass


# ── Public API ─────────────────────────────────────────────────────────────────


def find_usb_device(vendor_id: Optional[int], product_id: Optional[int], logger):
    """
    Auto-detect the SPL meter. Returns a device object with a .read(logger) method.
    Exits the process if no supported device is found.
    """
    vid = vendor_id
    pid = product_id

    # ── Try serial CH340 variant first (no pyusb required) ────────────────────
    if vid is None or (vid == SERIAL_VENDOR_ID and pid == SERIAL_PRODUCT_ID):
        import glob

        ports = sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/cu.usbserial-*"))
        if ports:
            if pyserial is None:
                logger.error("pyserial is not installed. Run: pip install pyserial")
                sys.exit(1)
            port = ports[0]
            logger.info(
                f"Serial SPL meter (CH340) found at {port} ({SERIAL_BAUD} baud, 6-byte packets)"
            )
            return _SerialDevice(port, SERIAL_BAUD)

    # ── Fall back to HID device ────────────────────────────────────────────────
    if usb is None:
        logger.error("pyusb is not installed and no serial device found.")
        sys.exit(1)
    hid_vid = vid or HID_VENDOR_ID
    hid_pid = pid or HID_PRODUCT_ID
    dev = usb.core.find(idVendor=hid_vid, idProduct=hid_pid)
    if dev is None:
        logger.error(
            f"SPL meter not found. Tried serial ({SERIAL_PORT}) and "
            f"HID (VID=0x{hid_vid:04X}, PID=0x{hid_pid:04X}). Exiting."
        )
        sys.exit(1)
    try:
        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
    except Exception:
        pass
    logger.info(f"HID SPL meter found (VID=0x{hid_vid:04X}, PID=0x{hid_pid:04X})")
    return _HidDevice(dev)


def read_spl_value(device, logger) -> Optional[float]:
    return device.read(logger)
