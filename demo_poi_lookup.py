# -*- coding: utf-8 -*-
"""โปรแกรมแสดงผลการค้นหาจุดสนใจจากพิกัดที่สุ่มขึ้น

วิธีใช้
    python demo_poi_lookup.py
    python demo_poi_lookup.py --count 50 --detail
    python demo_poi_lookup.py --mode bbox --seed 42
    python demo_poi_lookup.py --json > result.json

ค่าตั้งต้นคือสุ่มจุด 20 จุด โดยหยิบระเบียนจริงจาก poi:rows มาแล้วขยับพิกัด
เล็กน้อย เพื่อให้เหมือนพิกัดที่รับมาจากเครื่องนำทางซึ่งไม่ตรงกับจุดสนใจพอดี
โหมด bbox จะสุ่มพิกัดทั่วกรอบประเทศไทย ซึ่งหลายจุดจะไม่พบจุดสนใจในรัศมี
"""

import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import poi_lookup
from poi_lookup import PoiLookup, to_dlt_province

DEFAULT_URL = "redis://redis-poi:6379/0"

# กรอบพิกัดของประเทศไทยโดยประมาณ ใช้กับโหมด bbox
THAI_BBOX = {"lat_min": 5.6, "lat_max": 20.5, "lon_min": 97.3, "lon_max": 105.7}

# ระยะที่ขยับพิกัดในโหมด poi ประมาณ 200 เมตร
JITTER_DEG = 0.002


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


def sample_points(conn, count, mode):
    """คืนรายการพิกัด (lat, lon) ที่จะนำไปค้นหา"""
    if mode == "bbox":
        return [(random.uniform(THAI_BBOX["lat_min"], THAI_BBOX["lat_max"]),
                 random.uniform(THAI_BBOX["lon_min"], THAI_BBOX["lon_max"]))
                for _ in range(count)]

    raw = conn.hrandfield("poi:rows", count, withvalues=True)
    rows = _as_pairs(raw)
    points = []
    for _, value in rows:
        row = json.loads(value)
        points.append((float(row["y"]) + random.uniform(-JITTER_DEG, JITTER_DEG),
                       float(row["x"]) + random.uniform(-JITTER_DEG, JITTER_DEG)))
    return points


def describe(conn, lookup, lat, lon):
    """ผลการค้นหาหนึ่งจุด พร้อมรายละเอียดที่ต้องอ่านจากตารางรหัสเพิ่มเติม"""
    result = {"lat": lat, "lon": lon, "poi": lookup.nearest(lat, lon)}
    if result["poi"] is None:
        return result

    # ขอรหัสจุดกับระยะห่างมาแสดง เพราะ nearest() คืนเฉพาะข้อมูลที่ใช้งานต่อ
    found = conn.geosearch("poi:geo", longitude=lon, latitude=lat,
                           radius=lookup.radius_km, unit="km",
                           sort="ASC", count=1, withdist=True)
    if found:
        result["member"] = found[0][0]
        result["dist_km"] = float(found[0][1])
        row = json.loads(conn.hget("poi:rows", result["member"]))
        result["code_name"] = conn.hget("poi:code_names", row["poiCode"])
        result["local_name"] = conn.hget("poi:local_code_names", row["localCode"])
        # brandId เป็น 0 หมายถึงไม่มีแบรนด์ ไม่ใช่รหัสที่ต้องนำไปค้นหา
        if row["brandId"] != "0":
            result["brand_name"] = conn.hget("poi:brand_names", row["brandId"])
    return result


def print_compact(results, lookup):
    print("%3s %11s %10s %8s %4s  %s"
          % ("ที่", "longitude", "latitude", "ห่าง กม.", "จว.", "ที่อยู่"))
    print("-" * 78)
    for order, item in enumerate(results, 1):
        if item["poi"] is None:
            print("%3d %11.6f %10.6f %8s %4s  ไม่พบจุดสนใจในรัศมี %s กิโลเมตร"
                  % (order, item["lon"], item["lat"], "-", "-", lookup.radius_km))
            continue
        print("%3d %11.6f %10.6f %8.3f %4s  %s"
              % (order, item["lon"], item["lat"], item.get("dist_km", 0.0),
                 to_dlt_province(item["poi"]["province_code"]),
                 lookup.address(item["poi"])))


def print_detail(results, lookup):
    for order, item in enumerate(results, 1):
        print("[%2d] พิกัดที่สุ่ม  %.6f, %.6f" % (order, item["lon"], item["lat"]))
        if item["poi"] is None:
            print("     ไม่พบจุดสนใจในรัศมี %s กิโลเมตร" % lookup.radius_km)
            print("")
            continue
        poi = item["poi"]
        dopa = poi["province_code"]
        print("     จุดที่พบ     %s  (รหัสจุด %s  ห่าง %.3f กิโลเมตร)"
              % (poi["name"], item.get("member", "-"), item.get("dist_km", 0.0)))
        print("     ที่อยู่       %s" % lookup.address(poi))
        print("     ประเภท      %s / %s"
              % (item.get("code_name") or "-", item.get("local_name") or "-"))
        if item.get("brand_name"):
            print("     แบรนด์      %s" % item["brand_name"])
        print("     จังหวัด      %s   รหัส DOPA %s   รหัสกรมการขนส่งทางบก %s"
              % (poi["province"], dopa, to_dlt_province(dopa)))
        print("")


def print_summary(results, lookup):
    found = [item for item in results if item["poi"] is not None]
    print("-" * 78)
    print("พบจุดสนใจ %d จาก %d จุด" % (len(found), len(results)))
    if not found:
        return

    dists = sorted(item.get("dist_km", 0.0) for item in found)
    print("ระยะห่าง  ใกล้สุด %.3f  มัธยฐาน %.3f  ไกลสุด %.3f กิโลเมตร"
          % (dists[0], dists[len(dists) // 2], dists[-1]))

    counts = {}
    for item in found:
        name = item["poi"]["province"] or "ไม่ทราบจังหวัด"
        counts[name] = counts.get(name, 0) + 1
    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    print("จังหวัดที่พบ %d จังหวัด  %s"
          % (len(counts), "  ".join("%s %d" % pair for pair in ranked[:8])))
    print("ตัวนับของ PoiLookup  %s" % lookup.stats)


def _as_pairs(raw):
    """คืนผลของ HRANDFIELD WITHVALUES เป็นรายการคู่ ไม่ว่า redis-py จะคืนรูปแบบใด"""
    if not raw:
        return []
    if isinstance(raw[0], (list, tuple)):
        return [tuple(item) for item in raw]
    return list(zip(raw[0::2], raw[1::2]))


def main(argv=None):
    # ตั้งก่อนสร้างตัวแยกอาร์กิวเมนต์ เพราะคอนโซลของ Windows ตั้งต้นเป็นรหัส
    # อักขระที่พิมพ์ภาษาไทยไม่ได้ และ --help พิมพ์ออกจอทันทีที่แยกอาร์กิวเมนต์
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="แสดงผลการค้นหาจุดสนใจจากพิกัดที่สุ่มขึ้น")
    parser.add_argument("--url",
                        default=os.environ.get("POI_REDIS_URL") or DEFAULT_URL,
                        help="ที่อยู่ Redis ค่าตั้งต้นคือ %s" % DEFAULT_URL)
    parser.add_argument("--count", type=int, default=20,
                        help="จำนวนจุดที่สุ่ม ค่าตั้งต้นคือ 20")
    parser.add_argument("--radius", type=float, default=None,
                        help="รัศมีการค้นหาเป็นกิโลเมตร ค่าตั้งต้นคือ 1.2")
    parser.add_argument("--mode", choices=("poi", "bbox"), default="poi",
                        help="poi สุ่มจากระเบียนจริง  bbox สุ่มทั่วกรอบประเทศไทย")
    parser.add_argument("--seed", type=int, default=None,
                        help="ตั้งค่าเริ่มของตัวสุ่มเพื่อให้ได้ผลเดิมซ้ำได้")
    parser.add_argument("--detail", action="store_true",
                        help="แสดงรายละเอียดของแต่ละจุดแทนตารางย่อ")
    parser.add_argument("--json", action="store_true",
                        help="พิมพ์ผลเป็น JSON สำหรับส่งต่อโปรแกรมอื่น")
    args = parser.parse_args(argv)

    if args.count < 1:
        print("จำนวนจุดต้องมากกว่า 0")
        return 2

    conn, error = connect(args.url)
    if conn is None:
        print("เชื่อมต่อ Redis ที่ %s ไม่ได้" % args.url)
        print("  สาเหตุ %s" % error)
        return 1

    random.seed(args.seed)
    lookup = PoiLookup(url=args.url, radius_km=args.radius)
    points = sample_points(conn, args.count, args.mode)
    results = [describe(conn, lookup, lat, lon) for lat, lon in points]

    if args.json:
        print(json.dumps({"url": args.url, "mode": args.mode,
                          "radius_km": lookup.radius_km,
                          "seed": args.seed, "results": results},
                         ensure_ascii=False, indent=2))
        return 0

    print("=" * 78)
    print("ผลการค้นหาจุดสนใจจากพิกัดที่สุ่ม %d จุด" % args.count)
    print("Redis  %s" % args.url)
    print("รัศมี %s กิโลเมตร   วิธีสุ่ม %s   seed %s"
          % (lookup.radius_km, args.mode,
             args.seed if args.seed is not None else "ไม่ได้ตั้ง"))
    print("=" * 78)

    if args.detail:
        print_detail(results, lookup)
    else:
        print_compact(results, lookup)
    print_summary(results, lookup)
    return 0


if __name__ == "__main__":
    sys.exit(main())
