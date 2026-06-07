# pylint: disable=import-error
import time
from machine import I2C, Pin

BUS_ID = 1
SDA_PIN = 6
SCL_PIN = 7
FREQ = 100_000

ADDRS = (0x43, 0x42, 0x40, 0x41, 0x44, 0x45)
REG_SHUNT_V = 0x01
REG_BUS_V = 0x02


def u16_be(b):
    return (b[0] << 8) | b[1]


def s16(v):
    if v & 0x8000:
        return v - 0x10000
    return v


def main():
    sda = Pin(SDA_PIN, Pin.IN, Pin.PULL_UP)
    scl = Pin(SCL_PIN, Pin.IN, Pin.PULL_UP)
    i2c = I2C(BUS_ID, sda=sda, scl=scl, freq=FREQ)
    print(f"I2C bus={BUS_ID} SDA=GP{SDA_PIN} SCL=GP{SCL_PIN} freq={FREQ}")
    scanned = i2c.scan()
    print("scan:", [hex(x) for x in scanned])

    addr = 0
    for a in ADDRS:
        try:
            _ = i2c.readfrom_mem(a, REG_BUS_V, 2)
            addr = a
            break
        except Exception:
            pass

    if addr == 0:
        print("INA219 not reachable at known addresses")
        return

    print("INA219 addr:", hex(addr))
    while True:
        bus_raw = u16_be(i2c.readfrom_mem(addr, REG_BUS_V, 2))
        shunt_raw = s16(u16_be(i2c.readfrom_mem(addr, REG_SHUNT_V, 2)))
        bus_v = ((bus_raw >> 3) * 4) / 1000.0
        current_ma = shunt_raw
        print(f"bus={bus_v:.3f}V current={current_ma}mA raw_bus=0x{bus_raw:04X}")
        time.sleep(2)


main()
