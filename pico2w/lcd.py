# Waveshare Pico LCD 1.3" (240x240, ST7789) — minimal driver
# Pins: DC=GP8, CS=GP9, CLK=GP10, DIN=GP11, RST=GP12, BL=GP13
# Uses SPI1 — no conflict with ADC on GP26
import struct
import time
from machine import Pin, SPI
import framebuf

# RGB565 colors (byte-swapped for LE framebuf → BE display)
BLACK = 0x0000
WHITE = 0xFFFF
RED = 0x00F8
GREEN = 0xE007
BLUE = 0x1F00
YELLOW = 0xE0FF
CYAN = 0xFF07
GRAY = 0x1084
ORANGE = 0x20FD


class LCD:
    W, H = 240, 240

    def __init__(self):
        self.spi = SPI(1, 62_500_000, polarity=1, phase=1,
                       sck=Pin(10), mosi=Pin(11))
        self.cs = Pin(9, Pin.OUT, value=1)
        self.dc = Pin(8, Pin.OUT)
        self.rst = Pin(12, Pin.OUT, value=1)
        self.bl = Pin(13, Pin.OUT, value=1)

        self.buf = bytearray(self.W * self.H * 2)  # ~112 KB
        self.fb = framebuf.FrameBuffer(self.buf, self.W, self.H,
                                       framebuf.RGB565)
        self._init_hw()

    def _cmd(self, c):
        self.cs(0)
        self.dc(0)
        self.spi.write(bytes([c]))
        self.cs(1)

    def _dat(self, d):
        self.cs(0)
        self.dc(1)
        self.spi.write(d)
        self.cs(1)

    def _init_hw(self):
        self.rst(1)
        time.sleep_ms(5)
        self.rst(0)
        time.sleep_ms(5)
        self.rst(1)
        time.sleep_ms(10)

        for c, d in [
            (0x36, b'\x00'), (0x3A, b'\x05'),
            (0xB2, b'\x0C\x0C\x00\x33\x33'),
            (0xB7, b'\x35'), (0xBB, b'\x19'),
            (0xC0, b'\x2C'), (0xC2, b'\x01'),
            (0xC3, b'\x12'), (0xC4, b'\x20'),
            (0xC6, b'\x0F'), (0xD0, b'\xA4\xA1'),
            (0xE0, b'\xD0\x04\x0D\x11\x13\x2B\x3F'
                   b'\x54\x4C\x18\x0D\x0B\x1F\x23'),
            (0xE1, b'\xD0\x04\x0C\x11\x13\x2C\x3F'
                   b'\x44\x51\x2F\x1F\x1F\x20\x23'),
        ]:
            self._cmd(c)
            self._dat(d)

        self._cmd(0x21)   # inversion ON
        self._cmd(0x11)   # sleep OUT
        time.sleep_ms(100)
        self._cmd(0x29)   # display ON

    def show(self):
        self._cmd(0x2A)
        self._dat(struct.pack('>HH', 0, self.W - 1))
        self._cmd(0x2B)
        self._dat(struct.pack('>HH', 0, self.H - 1))
        self._cmd(0x2C)
        self.cs(0)
        self.dc(1)
        self.spi.write(self.buf)
        self.cs(1)

    def clear(self, c=BLACK):
        self.fb.fill(c)

    def text(self, s, x, y, c=WHITE):
        self.fb.text(s, x, y, c)

    def hline(self, x, y, w, c=GRAY):
        self.fb.hline(x, y, w, c)

    def fill_rect(self, x, y, w, h, c):
        self.fb.fill_rect(x, y, w, h, c)

    def vline(self, x, y, h, c=WHITE):
        self.fb.vline(x, y, h, c)

    def pixel(self, x, y, c=WHITE):
        self.fb.pixel(x, y, c)

    def line(self, x0, y0, x1, y1, c=WHITE):
        self.fb.line(x0, y0, x1, y1, c)

    def rect(self, x, y, w, h, c=WHITE):
        self.fb.rect(x, y, w, h, c)

    def backlight(self, on=True):
        self.bl(1 if on else 0)
