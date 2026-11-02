"""ตัวค้นหาจุดสนใจที่ใกล้พิกัดที่สุดจาก POI Redis

สร้างตามคู่มือการใช้งาน POI Redis รุ่นข้อมูล 2026-10
ทุกความผิดพลาดคืนค่า None โดยไม่โยนข้อยกเว้น ตามหัวข้อ 6 ของคู่มือ
"""

import json
import os
import threading
from collections import OrderedDict

try:
    import redis
except ImportError:  # ยังไม่ติดตั้ง redis-py การค้นหาจะคืนค่าว่างเสมอ
    redis = None

DATA_VERSION = "2026-10"

# รหัสจังหวัดระบบ DOPA / TIS-1099 ซึ่งเป็นระบบที่ใช้ในฟิลด์ adminId ของ POI
# ห้ามแก้บึงกาฬเป็น 97 ในตารางนี้ ดูเหตุผลในหัวข้อ 5 ของคู่มือ
DOPA_PROVINCES = {
    10: "กรุงเทพมหานคร", 11: "สมุทรปราการ", 12: "นนทบุรี", 13: "ปทุมธานี",
    14: "พระนครศรีอยุธยา", 15: "อ่างทอง", 16: "ลพบุรี", 17: "สิงห์บุรี",
    18: "ชัยนาท", 19: "สระบุรี", 20: "ชลบุรี", 21: "ระยอง", 22: "จันทบุรี",
    23: "ตราด", 24: "ฉะเชิงเทรา", 25: "ปราจีนบุรี", 26: "นครนายก",
    27: "สระแก้ว", 30: "นครราชสีมา", 31: "บุรีรัมย์", 32: "สุรินทร์",
    33: "ศรีสะเกษ", 34: "อุบลราชธานี", 35: "ยโสธร", 36: "ชัยภูมิ",
    37: "อำนาจเจริญ", 38: "บึงกาฬ", 39: "หนองบัวลำภู", 40: "ขอนแก่น",
    41: "อุดรธานี", 42: "เลย", 43: "หนองคาย", 44: "มหาสารคาม",
    45: "ร้อยเอ็ด", 46: "กาฬสินธุ์", 47: "สกลนคร", 48: "นครพนม",
    49: "มุกดาหาร", 50: "เชียงใหม่", 51: "ลำพูน", 52: "ลำปาง",
    53: "อุตรดิตถ์", 54: "แพร่", 55: "น่าน", 56: "พะเยา", 57: "เชียงราย",
    58: "แม่ฮ่องสอน", 60: "นครสวรรค์", 61: "อุทัยธานี", 62: "กำแพงเพชร",
    63: "ตาก", 64: "สุโขทัย", 65: "พิษณุโลก", 66: "พิจิตร", 67: "เพชรบูรณ์",
    70: "ราชบุรี", 71: "กาญจนบุรี", 72: "สุพรรณบุรี", 73: "นครปฐม",
    74: "สมุทรสาคร", 75: "สมุทรสงคราม", 76: "เพชรบุรี", 77: "ประจวบคีรีขันธ์",
    80: "นครศรีธรรมราช", 81: "กระบี่", 82: "พังงา", 83: "ภูเก็ต",
    84: "สุราษฎร์ธานี", 85: "ระนอง", 86: "ชุมพร", 90: "สงขลา", 91: "สตูล",
    92: "ตรัง", 93: "พัทลุง", 94: "ปัตตานี", 95: "ยะลา", 96: "นราธิวาส",
}

# รหัสจังหวัดที่สองระบบไม่ตรงกัน คือบึงกาฬเพียงจังหวัดเดียว
_DLT_OVERRIDE = {38: 97}

DEFAULT_RADIUS_KM = 1.2
DEFAULT_CACHE_MAX = 50000
CACHE_PRECISION = 3  # ปัดพิกัดเป็นทศนิยม 3 ตำแหน่ง ได้ช่องประมาณ 110 เมตร

# เวลารอของการเชื่อมต่อที่โปรแกรมเรียกใช้โดยตรงผ่าน connect()
DEFAULT_TIMEOUT = 5.0
# เวลารอของคอนเนกชันภายใน PoiLookup ซึ่งสั้นกว่า เพราะการค้นหาที่ช้าเกิน
# เท่ากับคืนค่าว่างอยู่แล้วตามหัวข้อ 6 ของคู่มือ จึงไม่ควรถ่วงผู้เรียก
LOOKUP_TIMEOUT = 2.0


def to_dlt_province(code):
    """แปลงรหัสจังหวัด DOPA เป็นรหัสของกรมการขนส่งทางบก"""
    if code is None:
        return None
    try:
        code = int(code)
    except (TypeError, ValueError):
        return None
    return _DLT_OVERRIDE.get(code, code)


def province_code_from_admin_id(admin_id):
    """ถอดรหัสจังหวัดจากสองหลักแรกของ adminId คืน None เมื่อถอดไม่ได้"""
    if admin_id is None:
        return None
    text = str(admin_id).strip()
    if len(text) < 2 or not text[:2].isdigit():
        return None
    code = int(text[:2])
    return code if code in DOPA_PROVINCES else None


def connect(url, timeout=DEFAULT_TIMEOUT):
    """คืนคู่ของตัวเชื่อมต่อกับข้อความผิดพลาด โดยทดสอบด้วย PING ก่อนคืน

    ใช้ร่วมกันทั้งชุดทดสอบและโปรแกรมแสดงผล เพื่อให้ทั้งสองเชื่อมต่อด้วยค่า
    ชุดเดียวกัน ต่างจาก PoiLookup.client() ที่รอสั้นกว่าและกลืนข้อผิดพลาด
    ทั้งหมดตามหัวข้อ 6 ของคู่มือ
    """
    if redis is None:
        return None, "ยังไม่ได้ติดตั้งไลบรารี redis ให้สั่ง pip install redis"
    try:
        conn = redis.Redis.from_url(
            url, decode_responses=True,
            socket_timeout=timeout, socket_connect_timeout=timeout,
        )
        conn.ping()
        return conn, None
    except Exception as exc:
        return None, "%s %s" % (type(exc).__name__, exc)


class PoiLookup:
    """ค้นจุดสนใจที่ใกล้พิกัดที่สุด พร้อมแปลงรหัสจังหวัดและจำผลไว้ชั่วคราว"""

    def __init__(self, url=None, radius_km=None, cache_max=None,
                 geo_key=None, rows_key=None, admin_key=None):
        env = os.environ.get
        self.url = url if url is not None else env("POI_REDIS_URL")
        self.radius_km = _as_float(radius_km, env("POI_RADIUS_KM"), DEFAULT_RADIUS_KM)
        self.cache_max = _as_int(cache_max, env("POI_CACHE_MAX"), DEFAULT_CACHE_MAX)
        self.geo_key = geo_key or env("POI_GEO_KEY") or "poi:geo"
        self.rows_key = rows_key or env("POI_ROWS_KEY") or "poi:rows"
        self.admin_key = admin_key or env("POI_ADMIN_KEY") or "poi:admin_names"

        self._cache = OrderedDict()
        self._lock = threading.Lock()
        self._client = None
        # ตัวนับไว้แยกกรณีบริการล่มออกจากกรณีค้นไม่พบ ตามหัวข้อ 6 ของคู่มือ
        self.stats = {"hits": 0, "misses": 0, "not_found": 0, "errors": 0, "disabled": 0}

    @property
    def enabled(self):
        return bool(self.url) and redis is not None

    def client(self):
        """คืนตัวเชื่อมต่อ Redis หรือ None เมื่อปิดการทำงานหรือเชื่อมต่อไม่ได้"""
        if not self.enabled:
            return None
        if self._client is None:
            try:
                self._client = redis.Redis.from_url(
                    self.url, decode_responses=True,
                    socket_timeout=LOOKUP_TIMEOUT,
                    socket_connect_timeout=LOOKUP_TIMEOUT,
                )
            except Exception:
                self.stats["errors"] += 1
                return None
        return self._client

    def nearest(self, lat, lon):
        """จุดที่ใกล้พิกัดที่สุดในรัศมีที่ตั้งไว้ หรือ None

        ลำดับอาร์กิวเมนต์เป็น lat ก่อน lon ซึ่งสลับกับ GEOSEARCH
        """
        if not self.enabled:
            self.stats["disabled"] += 1
            return None
        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            self.stats["errors"] += 1
            return None

        cache_key = (round(lat, CACHE_PRECISION), round(lon, CACHE_PRECISION))
        with self._lock:
            if cache_key in self._cache:
                self._cache.move_to_end(cache_key)
                self.stats["hits"] += 1
                return self._cache[cache_key]

        self.stats["misses"] += 1
        poi = self._fetch(lat, lon)
        if poi is None:
            return None
        self._remember(cache_key, poi)
        return poi

    def address(self, poi):
        """ข้อความที่อยู่จาก name street province เว้นวรรคระหว่างกัน"""
        if not poi:
            return ""
        parts = [poi.get("name"), poi.get("street"), poi.get("province")]
        return " ".join(p for p in parts if p)

    def clear_cache(self):
        with self._lock:
            self._cache.clear()

    def _fetch(self, lat, lon):
        conn = self.client()
        if conn is None:
            return None
        try:
            found = conn.geosearch(
                self.geo_key, longitude=lon, latitude=lat,
                radius=self.radius_km, unit="km", sort="ASC", count=1,
            )
            if not found:
                self.stats["not_found"] += 1
                return None
            raw = conn.hget(self.rows_key, found[0])
            if not raw:
                self.stats["not_found"] += 1
                return None
            row = json.loads(raw)
            admin_id = row.get("adminId")
            code = province_code_from_admin_id(admin_id)
            return {
                "name": row.get("name") or row.get("nameEng"),
                "street": row.get("street"),
                "province": self._province_name(conn, admin_id, code),
                "province_code": code,
            }
        except Exception:
            self.stats["errors"] += 1
            return None

    def _province_name(self, conn, admin_id, code):
        if admin_id:
            try:
                raw = conn.hget(self.admin_key, admin_id)
                if raw:
                    return json.loads(raw).get("NAME1") or DOPA_PROVINCES.get(code)
            except Exception:
                self.stats["errors"] += 1
        return DOPA_PROVINCES.get(code)

    def _remember(self, cache_key, poi):
        with self._lock:
            self._cache[cache_key] = poi
            self._cache.move_to_end(cache_key)
            while len(self._cache) > self.cache_max:
                self._cache.popitem(last=False)


def _as_float(explicit, env_value, default):
    for candidate in (explicit, env_value):
        if candidate is None or candidate == "":
            continue
        try:
            return float(candidate)
        except (TypeError, ValueError):
            continue
    return default


def _as_int(explicit, env_value, default):
    value = _as_float(explicit, env_value, default)
    return int(value)
