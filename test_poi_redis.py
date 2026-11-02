# -*- coding: utf-8 -*-
"""ชุดทดสอบการทำงานของ POI Redis ตามคู่มือรุ่นข้อมูล 2026-10

วิธีใช้
    python test_poi_redis.py
    python test_poi_redis.py --url redis://redis-poi:6379/0
    python test_poi_redis.py --quick        ข้ามการทดสอบที่ใช้เวลานาน
    python test_poi_redis.py --offline      รันเฉพาะส่วนที่ไม่ต้องมี Redis

ต้องติดตั้ง  pip install redis
การทดสอบกลุ่ม offline เรื่องรหัสจังหวัดและสวิตช์ปิดการทำงาน รันได้โดยไม่ต้องมี Redis
"""

import argparse
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import poi_lookup
from poi_lookup import (
    DOPA_PROVINCES,
    PoiLookup,
    province_code_from_admin_id,
    to_dlt_province,
)

DEFAULT_URL = "redis://redis-poi:6379/0"
DATA_VERSION = "2026-10"

# จำนวนสมาชิกที่คู่มือหัวข้อ 2 ระบุไว้
EXPECTED_SIZES = {
    "poi:geo": 2165819,
    "poi:rows": 2165819,
    "poi:code_names": 111,
    "poi:local_code_names": 493,
    "poi:brand_names": 626,
    "poi:admin_names": 7436,
}
EXPECTED_TYPES = {
    "poi:geo": "zset",
    "poi:rows": "hash",
    "poi:code_names": "hash",
    "poi:local_code_names": "hash",
    "poi:brand_names": "hash",
    "poi:admin_names": "hash",
    "poi:loaded": "string",
}

# ฟิลด์ที่คู่มือหัวข้อ 3.1 ระบุว่ามีครบทุกระเบียน
REQUIRED_FIELDS = [
    "x", "y", "id", "name", "nameEng", "adminId",
    "poiCode", "localCode", "brandId", "parentId", "relType", "version",
]
OPTIONAL_FIELDS = [
    "street", "streetEng", "postRdT", "postRdE",
    "masterTbranch", "masterEbranch", "branch", "branchEng",
    "altname", "altnamEng",
]

# พิกัดตัวอย่างในหัวข้อ 3.3 ของคู่มือ
SAMPLE_LON, SAMPLE_LAT = 100.5692, 13.7278
SAMPLE_MEMBER = "137006"

TESTS = []
RESULTS = {"pass": 0, "fail": 0, "skip": 0}
NOTES = []


def test(name, group="online"):
    def wrap(fn):
        TESTS.append((name, group, fn))
        return fn
    return wrap


class Failure(AssertionError):
    pass


class Skip(Exception):
    pass


def ok(cond, msg):
    if not cond:
        raise Failure(msg)


def eq(actual, expected, msg):
    if actual != expected:
        raise Failure("%s  ได้ %r คาดว่า %r" % (msg, actual, expected))


def near(actual, expected, tol, msg):
    ok(abs(actual - expected) <= tol,
       "%s  ได้ %s คาดว่า %s +/- %s" % (msg, actual, expected, tol))


def note(text):
    NOTES.append(text)


def show(text):
    """พิมพ์ออกจอทันทีระหว่างที่เทสต์ยังทำงานอยู่ ต่างจาก note ที่พิมพ์เมื่อเทสต์จบ"""
    print(text)
    sys.stdout.flush()


# ---------------------------------------------------------------- กลุ่ม offline

@test("รหัสจังหวัด DOPA มี 77 จังหวัดและมีบึงกาฬเป็น 38", "offline")
def t_province_table(ctx):
    eq(len(DOPA_PROVINCES), 77, "จำนวนจังหวัดในตาราง")
    eq(DOPA_PROVINCES.get(38), "บึงกาฬ", "รหัส 38")
    ok(97 not in DOPA_PROVINCES, "รหัส 97 ไม่ควรอยู่ในตาราง DOPA")


@test("to_dlt_province แปลงบึงกาฬ 38 เป็น 97 และปล่อยรหัสอื่นไว้เดิม", "offline")
def t_to_dlt(ctx):
    eq(to_dlt_province(38), 97, "บึงกาฬ")
    eq(to_dlt_province("38"), 97, "บึงกาฬแบบข้อความ")
    for code in DOPA_PROVINCES:
        if code != 38:
            eq(to_dlt_province(code), code, "รหัส %d ต้องไม่เปลี่ยน" % code)
    eq(to_dlt_province(None), None, "ค่าว่าง")
    eq(to_dlt_province("ไม่ใช่เลข"), None, "ข้อความที่ไม่ใช่รหัส")


@test("province_code_from_admin_id ถอดสองหลักแรกและตรวจกับตาราง", "offline")
def t_province_from_admin(ctx):
    eq(province_code_from_admin_id("103302"), 10, "adminId กรุงเทพ")
    eq(province_code_from_admin_id("100107"), 10, "adminId พระนคร")
    eq(province_code_from_admin_id("830107"), 83, "adminId ภูเก็ต")
    eq(province_code_from_admin_id("380101"), 38, "adminId บึงกาฬ ต้องคงเป็น 38")
    eq(province_code_from_admin_id("280101"), None, "รหัสจังหวัดที่ไม่มีจริง")
    eq(province_code_from_admin_id("0"), None, "สั้นเกินไป")
    eq(province_code_from_admin_id(""), None, "ข้อความว่าง")
    eq(province_code_from_admin_id(None), None, "ค่าว่าง")


@test("ลำดับการแปลงบึงกาฬถูกต้องคือถอดเป็น 38 แล้วค่อยแปลงเป็น 97", "offline")
def t_bueng_kan_chain(ctx):
    code = province_code_from_admin_id("380101")
    eq(code, 38, "ขั้นถอดจาก adminId")
    eq(to_dlt_province(code), 97, "ขั้นแปลงเป็นรหัสกรมการขนส่งทางบก")


@test("เมื่อไม่ได้ตั้ง POI_REDIS_URL การค้นหาคืนค่าว่างโดยไม่เชื่อมต่อ", "offline")
def t_disabled(ctx):
    lookup = PoiLookup(url="")
    ok(not lookup.enabled, "ต้องอยู่ในสถานะปิดการทำงาน")
    eq(lookup.nearest(SAMPLE_LAT, SAMPLE_LON), None, "ผลการค้นหา")
    eq(lookup.client(), None, "ต้องไม่สร้างตัวเชื่อมต่อ")
    eq(lookup.stats["disabled"], 1, "ตัวนับกรณีปิดการทำงาน")


@test("ค่าตั้งต้นและตัวแปรสภาพแวดล้อมถูกอ่านตามคู่มือหัวข้อ 4.1", "offline")
def t_env_config(ctx):
    plain = PoiLookup(url="redis://example:6379/0")
    eq(plain.radius_km, 1.2, "รัศมีตั้งต้น")
    eq(plain.cache_max, 50000, "ขนาดตารางจำผลตั้งต้น")
    eq(plain.geo_key, "poi:geo", "คีย์ geo ตั้งต้น")
    eq(plain.rows_key, "poi:rows", "คีย์ rows ตั้งต้น")

    names = ("POI_RADIUS_KM", "POI_CACHE_MAX", "POI_GEO_KEY", "POI_ROWS_KEY")
    saved = dict((k, os.environ.get(k)) for k in names)
    try:
        os.environ["POI_RADIUS_KM"] = "2.5"
        os.environ["POI_CACHE_MAX"] = "100"
        os.environ["POI_GEO_KEY"] = "other:geo"
        os.environ["POI_ROWS_KEY"] = "other:rows"
        tuned = PoiLookup(url="redis://example:6379/0")
        eq(tuned.radius_km, 2.5, "รัศมีจากตัวแปรสภาพแวดล้อม")
        eq(tuned.cache_max, 100, "ขนาดตารางจำผลจากตัวแปรสภาพแวดล้อม")
        eq(tuned.geo_key, "other:geo", "คีย์ geo จากตัวแปรสภาพแวดล้อม")
        eq(tuned.rows_key, "other:rows", "คีย์ rows จากตัวแปรสภาพแวดล้อม")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@test("address ประกอบข้อความและข้ามฟิลด์ที่ไม่มี", "offline")
def t_address(ctx):
    lookup = PoiLookup(url="")
    eq(lookup.address({"name": "โรงแรมอะริสตั้น", "street": "ถนนสุขุมวิท",
                       "province": "กรุงเทพมหานคร", "province_code": 10}),
       "โรงแรมอะริสตั้น ถนนสุขุมวิท กรุงเทพมหานคร", "ระเบียนที่มีฟิลด์ครบ")
    eq(lookup.address({"name": "เกาะราชาเล็ก", "street": None,
                       "province": "ภูเก็ต", "province_code": 83}),
       "เกาะราชาเล็ก ภูเก็ต", "ระเบียนที่ไม่มีถนน")
    eq(lookup.address(None), "", "ค่าว่าง")


# ----------------------------------------------------------------- กลุ่ม online

@test("ติดต่อ Redis ได้และ poi:loaded มีค่าเป็น 1")
def t_ping(ctx):
    ok(ctx.conn.ping(), "คำสั่ง PING")
    eq(ctx.conn.get("poi:loaded"), "1", "ค่า poi:loaded")


@test("ชนิดของทุกคีย์ตรงตามคู่มือหัวข้อ 2")
def t_key_types(ctx):
    for key, expected in sorted(EXPECTED_TYPES.items()):
        eq(ctx.conn.type(key), expected, "ชนิดของคีย์ %s" % key)


@test("จำนวนสมาชิกของทุกคีย์ตรงตามคู่มือหัวข้อ 2")
def t_key_sizes(ctx):
    actual = {
        "poi:geo": ctx.conn.zcard("poi:geo"),
        "poi:rows": ctx.conn.hlen("poi:rows"),
        "poi:code_names": ctx.conn.hlen("poi:code_names"),
        "poi:local_code_names": ctx.conn.hlen("poi:local_code_names"),
        "poi:brand_names": ctx.conn.hlen("poi:brand_names"),
        "poi:admin_names": ctx.conn.hlen("poi:admin_names"),
    }
    for key, expected in sorted(EXPECTED_SIZES.items()):
        note("ขนาด %-22s %12s" % (key, "{:,}".format(actual[key])))
    for key, expected in sorted(EXPECTED_SIZES.items()):
        eq(actual[key], expected, "จำนวนสมาชิกของ %s" % key)


@test("poi:geo กับ poi:rows ใช้รหัสจุดเดียวกันและพิกัดตรงกัน")
def t_geo_rows_consistency(ctx):
    members = ctx.conn.hrandfield("poi:rows", 10)
    eq(len(members), 10, "จำนวนระเบียนที่สุ่มได้")
    for member in members:
        pos = ctx.conn.geopos("poi:geo", member)
        ok(pos and pos[0], "รหัสจุด %s ต้องมีพิกัดใน poi:geo" % member)
        row = json.loads(ctx.conn.hget("poi:rows", member))
        lon, lat = pos[0]
        # poi:geo เก็บพิกัดแบบ geohash 52 บิต จึงคลาดจากค่าในระเบียนได้เล็กน้อย
        near(lon, float(row["x"]), 1e-4, "longitude ของจุด %s" % member)
        near(lat, float(row["y"]), 1e-4, "latitude ของจุด %s" % member)


@test("ระเบียนใน poi:rows เป็น JSON ที่มีฟิลด์บังคับครบและรุ่นข้อมูลถูกต้อง")
def t_row_schema(ctx):
    rows = _as_pairs(ctx.conn.hrandfield("poi:rows", 40, withvalues=True))
    eq(len(rows), 40, "จำนวนระเบียนที่สุ่มได้")

    present = dict((f, 0) for f in OPTIONAL_FIELDS)
    for member, raw in rows:
        row = json.loads(raw)
        for field in REQUIRED_FIELDS:
            ok(field in row and row[field] != "",
               "ระเบียน %s ต้องมีฟิลด์ %s" % (member, field))
        eq(row["version"], DATA_VERSION, "รุ่นข้อมูลของระเบียน %s" % member)
        eq(row["id"], member, "ฟิลด์ id ต้องเท่ากับคีย์ย่อยของระเบียน %s" % member)
        ok(97.0 < float(row["x"]) < 106.0, "longitude ของ %s ต้องอยู่ในไทย" % member)
        ok(5.0 < float(row["y"]) < 21.0, "latitude ของ %s ต้องอยู่ในไทย" % member)
        ok(province_code_from_admin_id(row["adminId"]) is not None,
           "adminId %s ของระเบียน %s ต้องถอดเป็นรหัสจังหวัดได้"
           % (row["adminId"], member))
        for field in OPTIONAL_FIELDS:
            if row.get(field):
                present[field] += 1
    for field in OPTIONAL_FIELDS:
        note("ฟิลด์ทางเลือก %-14s พบ %2d จาก 40 ระเบียน" % (field, present[field]))


@test("GEOSEARCH คืนจุดที่ใกล้ที่สุดตามตัวอย่างในคู่มือหัวข้อ 3.3")
def t_geosearch_example(ctx):
    found = ctx.conn.geosearch(
        "poi:geo", longitude=SAMPLE_LON, latitude=SAMPLE_LAT,
        radius=1.2, unit="km", sort="ASC", count=3, withdist=True,
    )
    eq(len(found), 3, "จำนวนผลลัพธ์")
    members = [item[0] for item in found]
    dists = [float(item[1]) for item in found]
    for member, dist in zip(members, dists):
        note("จุด %-12s ระยะ %.4f กิโลเมตร" % (member, dist))
    eq(members[0], SAMPLE_MEMBER, "จุดที่ใกล้ที่สุด")
    near(dists[0], 0.0121, 0.002, "ระยะห่างของจุดที่ใกล้ที่สุด")
    eq(dists, sorted(dists), "ผลลัพธ์ต้องเรียงจากใกล้ไปไกล")


@test("HGET poi:rows ของจุดตัวอย่างให้ระเบียนตรงตามคู่มือ")
def t_row_example(ctx):
    row = json.loads(ctx.conn.hget("poi:rows", SAMPLE_MEMBER))
    eq(row["name"], "โรงแรมอะริสตั้น", "ชื่อสถานที่")
    eq(row["nameEng"], "ARISTON HOTEL", "ชื่อภาษาอังกฤษ")
    eq(row["street"], "ถนนสุขุมวิท", "ชื่อถนน")
    eq(row["adminId"], "103302", "รหัสพื้นที่")
    eq(row["poiCode"], "7314", "รหัสประเภทหลัก")
    eq(row["version"], DATA_VERSION, "รุ่นข้อมูล")


@test("ระเบียนที่มีฟิลด์ครบและระเบียนที่ไม่มีถนน ตรงตามคู่มือหัวข้อ 3.1")
def t_doc_examples(ctx):
    full = json.loads(ctx.conn.hget("poi:rows", "5015378"))
    eq(full["name"], "โดมิโน่ส์พิซซ่า", "ชื่อระเบียนตัวอย่างที่มีฟิลด์ครบ")
    eq(full["poiCode"], "7315", "poiCode")
    eq(full["localCode"], "21401", "localCode")
    eq(full["brandId"], "73150011", "brandId")
    eq(full["adminId"], "100107", "adminId")
    eq(full["street"], "ถนนราชดำเนินกลาง", "ชื่อถนน")

    island = json.loads(ctx.conn.hget("poi:rows", "1548129"))
    eq(island["name"], "เกาะราชาเล็ก", "ชื่อระเบียนที่เป็นเกาะ")
    ok("street" not in island, "ระเบียนที่เป็นเกาะต้องไม่มีฟิลด์ street")
    eq(island["altname"], "เกาะราชาน้อย", "ชื่อรอง")
    eq(island["brandId"], "0", "brandId ของสถานที่ที่ไม่มีแบรนด์")


@test("ตารางรหัสแปลงเป็นชื่อได้ตรงตามคู่มือหัวข้อ 3.2")
def t_code_tables(ctx):
    eq(ctx.conn.hget("poi:code_names", "7315"), "ร้านอาหาร", "poiCode 7315")
    eq(ctx.conn.hget("poi:local_code_names", "21401"),
       "ร้านอาหารที่มีหลายสาขาอื่นๆ", "localCode 21401")
    eq(ctx.conn.hget("poi:brand_names", "73150011"),
       "โดมิโน่ส์พิซซ่า", "brandId 73150011")

    samples = {
        "poi:code_names": {"9102": "เอทีเอ็ม", "9111": "ศาลยุติธรรม",
                           "9591": "สุสาน", "9709": "หมู่บ้านหรือย่าน",
                           "9972": "สหกรณ์"},
        "poi:brand_names": {"73260011": "ร้านยาเอ็กซ์ต้าพลัส",
                            "95900203": "เดอะพอยต์คอนโด",
                            "99200032": "อินทรประกันภัย"},
    }
    for key, mapping in sorted(samples.items()):
        for code, expected in sorted(mapping.items()):
            eq(ctx.conn.hget(key, code), expected, "%s รหัส %s" % (key, code))

    for code in ("11060", "21021", "24032", "36020"):
        ok(ctx.conn.hget("poi:local_code_names", code),
           "localCode %s ต้องมีชื่อกำกับ" % code)

    # brandId เป็น 0 หมายถึงไม่มีแบรนด์ ไม่ใช่รหัสที่ต้องนำไปค้นหา
    ok(not ctx.conn.hexists("poi:brand_names", "0"),
       "poi:brand_names ไม่ควรมีรหัส 0")


@test("poi:admin_names ให้ชื่อสามระดับทั้งไทยและอังกฤษ")
def t_admin_names(ctx):
    admin = json.loads(ctx.conn.hget("poi:admin_names", "100107"))
    eq(admin["ADMIN_ID"], "100107", "ADMIN_ID")
    eq(admin["NAME1"], "กรุงเทพมหานคร", "ชื่อจังหวัด")
    eq(admin["NAME2"], "พระนคร", "ชื่ออำเภอ")
    eq(admin["NAME3"], "บวรนิเวศ", "ชื่อตำบล")
    eq(admin["NAME_ENG1"], "BANGKOK", "ชื่อจังหวัดภาษาอังกฤษ")
    eq(admin["ADMIN_ID1"], "10", "รหัสจังหวัดระบบ DOPA")
    eq(admin["VERSION"], DATA_VERSION, "รุ่นข้อมูล")
    eq(province_code_from_admin_id(admin["ADMIN_ID"]), int(admin["ADMIN_ID1"]),
       "สองหลักแรกของ ADMIN_ID ต้องตรงกับ ADMIN_ID1")


@test("ADMIN_ID1 ของระเบียนที่สุ่มจาก poi:admin_names เป็นรหัสจังหวัดที่รู้จัก")
def t_admin_province_codes(ctx):
    pairs = _as_pairs(ctx.conn.hrandfield("poi:admin_names", 200, withvalues=True))
    seen = set()
    for admin_id, raw in pairs:
        admin = json.loads(raw)
        code = int(admin["ADMIN_ID1"])
        ok(code in DOPA_PROVINCES,
           "รหัสจังหวัด %d ของ %s ไม่อยู่ในตาราง DOPA" % (code, admin_id))
        eq(DOPA_PROVINCES[code], admin["NAME1"], "ชื่อจังหวัดของรหัส %d" % code)
        seen.add(code)
    note("สุ่ม %d ระเบียน พบ %d จังหวัด" % (len(pairs), len(seen)))


@test("PoiLookup.nearest ให้ผลตรงตามตัวอย่างในคู่มือหัวข้อ 3.3")
def t_lookup_example(ctx):
    poi = ctx.lookup.nearest(SAMPLE_LAT, SAMPLE_LON)
    ok(poi is not None, "ต้องพบจุด")
    eq(sorted(poi.keys()), ["name", "province", "province_code", "street"],
       "คีย์ของผลลัพธ์")
    eq(poi["name"], "โรงแรมอะริสตั้น", "ชื่อสถานที่")
    eq(poi["street"], "ถนนสุขุมวิท", "ชื่อถนน")
    eq(poi["province"], "กรุงเทพมหานคร", "ชื่อจังหวัด")
    eq(poi["province_code"], 10, "รหัสจังหวัด")
    eq(ctx.lookup.address(poi), "โรงแรมอะริสตั้น ถนนสุขุมวิท กรุงเทพมหานคร",
       "ข้อความที่อยู่")
    eq(to_dlt_province(poi["province_code"]), 10, "รหัสกรมการขนส่งทางบก")


@test("สลับลำดับ lat กับ lon แล้วต้องไม่ได้ผลลัพธ์")
def t_swapped_arguments(ctx):
    swapped = ctx.lookup.nearest(SAMPLE_LON, SAMPLE_LAT)
    ok(swapped is None, "พิกัดที่สลับลำดับควรคืนค่าว่าง แต่ได้ %r" % (swapped,))


@test("พิกัดกลางทะเลอันดามันคืนค่าว่างเพราะไม่มีจุดในรัศมี")
def t_no_poi_in_radius(ctx):
    before = ctx.lookup.stats["not_found"]
    eq(ctx.lookup.nearest(8.5, 95.5), None, "ผลการค้นหากลางทะเล")
    eq(ctx.lookup.stats["not_found"], before + 1, "ตัวนับกรณีค้นไม่พบ")


@test("ตารางจำผลใช้ช่องขนาด 110 เมตร พิกัดในช่องเดียวกันได้ผลเดียวกัน")
def t_cache(ctx):
    lookup = PoiLookup(url=ctx.url)
    first = lookup.nearest(SAMPLE_LAT, SAMPLE_LON)
    ok(first is not None, "การค้นครั้งแรกต้องพบจุด")
    eq(lookup.stats, {"hits": 0, "misses": 1, "not_found": 0,
                      "errors": 0, "disabled": 0}, "ตัวนับหลังค้นครั้งแรก")

    # ต่างกันที่ทศนิยมตำแหน่งที่ห้า จึงอยู่ในช่องเดียวกันและต้องได้ผลจากตารางจำผล
    again = lookup.nearest(SAMPLE_LAT + 0.00002, SAMPLE_LON + 0.00002)
    ok(again is first, "พิกัดในช่องเดียวกันต้องได้ผลจากตารางจำผล")
    eq(lookup.stats["hits"], 1, "ตัวนับการใช้ตารางจำผล")
    eq(lookup.stats["misses"], 1, "ตัวนับการค้นจริง")

    # ห่างออกไปประมาณ 1 กิโลเมตร จึงเป็นช่องใหม่และต้องค้นจริงอีกครั้ง
    lookup.nearest(SAMPLE_LAT + 0.01, SAMPLE_LON)
    eq(lookup.stats["misses"], 2, "ตัวนับการค้นจริงหลังเปลี่ยนช่อง")


@test("ตารางจำผลไม่โตเกินค่า POI_CACHE_MAX")
def t_cache_limit(ctx):
    lookup = PoiLookup(url=ctx.url, cache_max=3)
    for step in range(6):
        lookup.nearest(SAMPLE_LAT + step * 0.01, SAMPLE_LON)
    ok(len(lookup._cache) <= 3,
       "ขนาดตารางจำผลต้องไม่เกิน 3 แต่ได้ %d" % len(lookup._cache))


@test("รัศมีที่แคบลงทำให้จุดที่ไกลกว่ารัศมีหายไป")
def t_radius(ctx):
    tight = PoiLookup(url=ctx.url, radius_km=0.001)
    eq(tight.radius_km, 0.001, "รัศมีที่ตั้งเอง")
    far = tight.nearest(13.0515, 101.2345)
    eq(far, None, "รัศมี 1 เมตรกลางทุ่งควรคืนค่าว่าง")


@test("เชื่อมต่อไปที่พอร์ตที่ไม่มีบริการแล้วคืนค่าว่างโดยไม่โยนข้อยกเว้น")
def t_connection_error(ctx):
    broken = PoiLookup(url="redis://127.0.0.1:1/0")
    eq(broken.nearest(SAMPLE_LAT, SAMPLE_LON), None, "ผลการค้นหาเมื่อติดต่อไม่ได้")
    eq(broken.stats["errors"], 1, "ตัวนับข้อผิดพลาด")


@test("ค้นจากพิกัดกลางเมืองใหญ่แล้วได้จังหวัดถูกต้องและเร็วพอใช้งาน")
def t_cities(ctx):
    if ctx.quick:
        raise Skip("ข้ามเพราะสั่ง --quick")
    cities = [
        ("กรุงเทพมหานคร", 13.7563, 100.5018, 10),
        ("เชียงใหม่", 18.7883, 98.9853, 50),
        ("ภูเก็ต", 7.8804, 98.3923, 83),
        ("ขอนแก่น", 16.4419, 102.8360, 40),
        ("หาดใหญ่", 7.0086, 100.4747, 90),
    ]
    lookup = PoiLookup(url=ctx.url)
    elapsed = []
    for name, lat, lon, expected_code in cities:
        start = time.time()
        poi = lookup.nearest(lat, lon)
        elapsed.append(time.time() - start)
        ok(poi is not None, "ต้องพบจุดใกล้ %s" % name)
        ok(poi["name"], "จุดใกล้ %s ต้องมีชื่อ" % name)
        eq(poi["province_code"], expected_code, "รหัสจังหวัดของจุดใกล้ %s" % name)
        note("ใกล้ %-16s %s" % (name, lookup.address(poi)))
    note("เวลาค้นหาเฉลี่ย %.1f มิลลิวินาที สูงสุด %.1f มิลลิวินาที"
         % (sum(elapsed) / len(elapsed) * 1000.0, max(elapsed) * 1000.0))


@test("สุ่มระเบียนจริง 30 จุดแล้วค้นจากพิกัดของตัวเองต้องได้จังหวัดเดียวกัน")
def t_round_trip(ctx):
    if ctx.quick:
        raise Skip("ข้ามเพราะสั่ง --quick")
    pairs = _as_pairs(ctx.conn.hrandfield("poi:rows", 30, withvalues=True))
    lookup = PoiLookup(url=ctx.url)
    for member, raw in pairs:
        row = json.loads(raw)
        poi = lookup.nearest(float(row["y"]), float(row["x"]))
        ok(poi is not None, "ต้องพบจุดใกล้พิกัดของระเบียน %s" % member)
        eq(poi["province_code"], province_code_from_admin_id(row["adminId"]),
           "รหัสจังหวัดของจุดที่ค้นจากพิกัดของระเบียน %s" % member)


@test("สุ่ม 20 จุดแล้วแสดงผลการ lookup")
def t_sample_twenty(ctx):
    rows = _as_pairs(ctx.conn.hrandfield("poi:rows", 20, withvalues=True))
    eq(len(rows), 20, "จำนวนจุดที่สุ่มได้")

    lookup = PoiLookup(url=ctx.url)
    found = 0
    elapsed = []

    # ตารางนี้พิมพ์ออกจอทันทีขณะค้นหา ไม่รอให้เทสต์จบเหมือนบรรทัดหมายเหตุอื่น
    show("")
    show("-" * 78)
    show("ผลการ lookup จุดสุ่ม 20 จุด  รัศมี %s กิโลเมตร" % lookup.radius_km)
    show("-" * 78)
    show("%3s %12s %11s %4s  %s"
         % ("ที่", "longitude", "latitude", "จว.", "สถานที่ ถนน จังหวัด"))
    for order, (member, raw) in enumerate(rows, 1):
        row = json.loads(raw)
        # ขยับพิกัดไม่เกินประมาณ 200 เมตร เพื่อให้เป็นการค้นจากจุดที่ไม่ตรงกับระเบียนพอดี
        lat = float(row["y"]) + random.uniform(-0.002, 0.002)
        lon = float(row["x"]) + random.uniform(-0.002, 0.002)
        start = time.time()
        poi = lookup.nearest(lat, lon)
        elapsed.append(time.time() - start)
        if poi is None:
            show("%3d %12.6f %11.6f %4s  ไม่พบจุดในรัศมี" % (order, lon, lat, "-"))
            continue
        found += 1
        show("%3d %12.6f %11.6f %4s  %s"
             % (order, lon, lat, to_dlt_province(poi["province_code"]),
                lookup.address(poi)))
    show("-" * 78)
    show("พบ %d จาก 20 จุด  เวลาค้นหาเฉลี่ย %.1f มิลลิวินาที  สูงสุด %.1f มิลลิวินาที"
         % (found, sum(elapsed) / len(elapsed) * 1000.0, max(elapsed) * 1000.0))
    show("")
    ok(found >= 18, "ควรพบจุดอย่างน้อย 18 จาก 20 แต่พบ %d" % found)


# ---------------------------------------------------------------------- ตัวช่วย

class Context(object):
    def __init__(self, conn, lookup, url, quick):
        self.conn = conn
        self.lookup = lookup
        self.url = url
        self.quick = quick


def _as_pairs(raw):
    """คืนผลของ HRANDFIELD WITHVALUES เป็นรายการคู่ ไม่ว่า redis-py จะคืนรูปแบบใด"""
    if not raw:
        return []
    if isinstance(raw[0], (list, tuple)):
        return [tuple(item) for item in raw]
    return list(zip(raw[0::2], raw[1::2]))


def connect(url):
    if poi_lookup.redis is None:
        return None, "ยังไม่ได้ติดตั้งไลบรารี redis ให้สั่ง pip install redis"
    try:
        conn = poi_lookup.redis.Redis.from_url(
            url, decode_responses=True,
            socket_timeout=5.0, socket_connect_timeout=5.0,
        )
        conn.ping()
        return conn, None
    except Exception as exc:
        return None, "%s %s" % (type(exc).__name__, exc)


def main(argv=None):
    parser = argparse.ArgumentParser(description="ทดสอบการทำงานของ POI Redis")
    parser.add_argument("--url",
                        default=os.environ.get("POI_REDIS_URL") or DEFAULT_URL,
                        help="ที่อยู่ Redis ค่าตั้งต้นคือ %s" % DEFAULT_URL)
    parser.add_argument("--quick", action="store_true",
                        help="ข้ามการทดสอบที่ใช้เวลานาน")
    parser.add_argument("--offline", action="store_true",
                        help="รันเฉพาะการทดสอบที่ไม่ต้องใช้ Redis")
    args = parser.parse_args(argv)

    random.seed()
    # คอนโซลของ Windows ตั้งต้นเป็นรหัสอักขระที่พิมพ์ภาษาไทยไม่ได้
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print("ทดสอบ POI Redis ที่ %s" % args.url)
    print("=" * 78)

    if args.offline:
        conn, error = None, "สั่ง --offline ไว้"
    else:
        conn, error = connect(args.url)
    if error:
        print("ไม่ได้ทดสอบกลุ่ม online  เหตุผล %s" % error)
        print("")

    lookup = PoiLookup(url=args.url) if conn is not None else None
    ctx = Context(conn, lookup, args.url, args.quick)

    for name, group, fn in TESTS:
        del NOTES[:]
        if group == "online" and conn is None:
            RESULTS["skip"] += 1
            print("ข้าม    %s" % name)
            continue
        try:
            fn(ctx)
        except Skip as exc:
            RESULTS["skip"] += 1
            print("ข้าม    %s  (%s)" % (name, exc))
            continue
        except Failure as exc:
            RESULTS["fail"] += 1
            print("ไม่ผ่าน  %s" % name)
            print("         %s" % exc)
        except Exception as exc:
            RESULTS["fail"] += 1
            print("ไม่ผ่าน  %s" % name)
            print("         ข้อยกเว้นที่ไม่คาดคิด %s %s" % (type(exc).__name__, exc))
        else:
            RESULTS["pass"] += 1
            print("ผ่าน    %s" % name)
        for line in NOTES:
            print("         %s" % line)

    print("=" * 78)
    print("ผ่าน %d  ไม่ผ่าน %d  ข้าม %d"
          % (RESULTS["pass"], RESULTS["fail"], RESULTS["skip"]))
    return 1 if RESULTS["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
