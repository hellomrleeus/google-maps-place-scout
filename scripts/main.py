#!/usr/bin/env python3
"""
Main entrypoint for Google Maps Place Scout & Corridor Planner Skill.
Orchestrates:
1. Google Places search along a unidirectional travel corridor in English.
2. Filtering out contracted and visited places (CRM & Google Sheet).
3. Agent-Native multimodal AI audit of places & storefront photos (Zero external API key).
4. Directional slice & cluster sweep algorithm (Anti-Shuttle "不要折返跑").
5. Full 30-stop slash-concatenated Google Maps URL (bypassing 10-stop limit).
6. Exporting strict 7-column English spreadsheet (No., Place Name, Address, Navigation Address, Opening Hours, Phone, Match Evidence).
"""

import os
import sys
import re
import json
import math
import argparse
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from filters import PlaceFilter
from places_searcher import PlacesSearcher
from directional_router import (
    DirectionalRouter,
    generate_corridor_probe_points,
    generate_radial_probe_points,
    haversine_distance_km
)
from place_auditor import PlaceAuditManager
from route_generator import RouteGenerator
from sheet_exporter import SheetExporter
from config_manager import (
    load_effective_config,
    run_interactive_setup,
    update_config_values,
    get_config_summary,
    get_default_config_path,
    get_effective_storage_paths,
    resolve_origin_input,
    get_temp_dir,
    get_user_cache_dir,
    extract_spreadsheet_id,
    USER_CONFIG_FILE
)

def parse_bearing(direction_arg: str, config_directions: dict) -> float:
    dir_key = direction_arg.lower().strip()
    if dir_key in config_directions:
        return float(config_directions[dir_key]["bearing"])
    try:
        return float(dir_key)
    except ValueError:
        print(f"[Warning] Unknown direction '{direction_arg}'. Defaulting to 'east' (90 degrees).")
        return 90.0

def main():
    parser = argparse.ArgumentParser(description="Google Maps Place Scout & Corridor Planner Skill")
    parser.add_argument("--setup", action="store_true", help="启动交互式配置向导设置 API Key 与 Google Sheet")
    parser.add_argument("--configure", action="store_true", help="非交互式更新配置模式 (供智能体或自动化脚本直接调用)")
    parser.add_argument("--set-api-key", type=str, default=None, help="设置 Google Maps API Key")
    parser.add_argument("--set-typesafe-key", "--set-jev-key", type=str, default=None, help="设置 TypeSafe API Key (用于 official Jev 决策模型)")
    parser.add_argument("--typesafe-key", "--jev-key", type=str, default=None, help="临时指定 TypeSafe API Key")
    parser.add_argument("--set-openrouter-key", type=str, default=None, help="设置 TypeSafe/OpenRouter API Key (用于 Jev 决策模型)")
    parser.add_argument("--openrouter-key", type=str, default=None, help="临时指定 TypeSafe/OpenRouter API Key")
    parser.add_argument("--set-sheet-url", type=str, default=None, help="设置 Google Apps Script Webhook URL")
    parser.add_argument("--set-origin", type=str, default=None, help="设置默认出发起点 (支持文本地址、Google 地图链接或经纬度坐标)")
    parser.add_argument("--check-config", action="store_true", help="查看当前持久化配置状态 (输出结构化 JSON 供智能体检查)")
    parser.add_argument("--sheet-url", type=str, default=None, help="Google Apps Script Webhook URL (例如: https://script.google.com/macros/s/.../exec)")
    parser.add_argument("--contracted-source", type=str, default=None, help="已签约场所数据源 (本地 Excel/CSV/JSON 路径或 REST API URL)")
    parser.add_argument("--visited-source", type=str, default=None, help="已拜访场所数据源 (本地 Excel/CSV/JSON 路径或 REST API URL)")
    parser.add_argument("--origin", type=str, default=None, help="出发起点 (支持 Google 地图链接、文本地址/商圈地标或 '纬度,经度' 坐标)")
    parser.add_argument("--exclude-regions", type=str, default=None, help="逗号分隔的避开/禁行区域或地标关键词 (例如 'scarborough,downtown')")
    parser.add_argument("--include-regions", type=str, default=None, help="逗号分隔的限定区域关键词 (例如 'markham')")
    parser.add_argument("--exclude-places", "--exclude-names", type=str, default=None, help="逗号分隔的特定排除场所名称或关键词 (例如 'Tim Hortons,Shell')")
    parser.add_argument("--include-places", "--include-names", type=str, default=None, help="逗号分隔的特定必选场所名称、Google 地图链接或地址 (例如 'Pilot Coffee Roasters')")
    parser.add_argument("--radius", type=float, default=None, help="搜索半径或探测纵深 (km，例如在XXXX附近 6 公里找)")
    parser.add_argument("--direction", type=str, default=None, help="Travel direction (east, northeast, north, south, west, radial/nearby, or bearing angle)")
    parser.add_argument("--count", type=int, default=None, help="Target number of confirmed places (default 30)")
    parser.add_argument("--origin-name", type=str, default=None, help="Starting location name")
    parser.add_argument("--origin-address", type=str, default=None, help="Starting address")
    parser.add_argument("--origin-lat", type=float, default=None, help="Starting latitude")
    parser.add_argument("--origin-lng", type=float, default=None, help="Starting longitude")
    parser.add_argument("--corridor-width", type=float, default=None, help="Lateral corridor width in km")
    parser.add_argument("--max-depth", type=float, default=None, help="Maximum along-track search depth in km")
    parser.add_argument("--departure-time", type=str, default=None, help="出发时间 (格式如 09:30，默认读取配置或 09:30)")
    parser.add_argument("--visit-date", type=str, default=None, help="拜访日期 (YYYY-MM-DD，也可输入 'today' / 'tomorrow'，默认自适应)")
    parser.add_argument("--allow-dinner-only", action="store_true", help="允许仅晚间/夜宵时段营业的场所 (默认自动排除白天未营业的场所)")
    parser.add_argument("--keep-closed", action="store_true", help="禁用开业状态校验 (保留公休与停业场所)")
    parser.add_argument("--dump-audit-only", action="store_true", help="Dump pending audit queue for agent inspection and pause")
    parser.add_argument("--search-center", type=str, default=None, help="目标搜索中心/商圈 (支持商圈地标、文本地址或 '纬度,经度' 坐标，解耦出发大本营)")
    parser.add_argument("--search-radius", type=float, default=None, help="目标搜索中心探测半径 (km，默认读取配置或 2.0 km)")
    parser.add_argument("--bounds", type=str, default=None, help="严格矩形边框过滤 'min_lat,min_lng,max_lat,max_lng'")
    parser.add_argument("--keywords", type=str, default=None, help="逗号分隔的自定义探测关键词列表 (覆盖或追加)")
    parser.add_argument("--place-types", "--types", type=str, default=None, help="逗号分隔的目标地点类型 (如 'cafe', 'gym', 'car_wash', 'dentist')")
    parser.add_argument("--criteria", type=str, default=None, help="自定义研判判定标准 (如 'has commercial espresso machine', 'offers oil change')")
    parser.add_argument("--template", type=str, default=None, help="判定标准预设模板 (general, coffee, auto, fitness, dining)")
    parser.add_argument("--config", type=str, default=None, help="Path to custom config.json")
    args = parser.parse_args()

    # Agent / Machine inspection of config status
    if args.check_config:
        status = get_config_summary(args.config)
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return status

    # Non-interactive configuration update (Agent-friendly)
    jev_key_to_set = args.set_typesafe_key or args.set_openrouter_key
    if args.configure or args.set_api_key is not None or jev_key_to_set is not None or args.set_sheet_url is not None or args.set_origin is not None:
        cfg = update_config_values(
            config_path=args.config,
            api_key=args.set_api_key,
            typesafe_key=jev_key_to_set,
            sheet_url=args.set_sheet_url,
            origin_str=args.set_origin
        )
        status = get_config_summary(args.config)
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return status

    # Interactive setup wizard
    if args.setup:
        run_interactive_setup(config_save_path=args.config)
        return

    # Load configuration
    cfg = load_effective_config(custom_config_path=args.config)
    openrouter_api_key = (
        args.openrouter_key
        or args.typesafe_key
        or cfg.get("typesafe_api_key")
        or cfg.get("openrouter_api_key")
        or os.environ.get("TYPESAFE_API_KEY")
        or os.environ.get("JEV_API_KEY")
        or os.environ.get("OPENROUTER_API_KEY", "")
    )

    # Resolve departure date
    today_dt = datetime.now()
    if not args.visit_date:
        target_mode = cfg.get("default_visit_target", "auto").lower().strip()
        if target_mode in ("tomorrow", "next_day"):
            visit_dt = today_dt + timedelta(days=1)
            date_label = "明天"
        elif target_mode in ("today", "same_day"):
            visit_dt = today_dt
            date_label = "今天"
        else:
            if today_dt.hour >= 18:
                visit_dt = today_dt + timedelta(days=1)
                date_label = "明天 (晚间自适应)"
            else:
                visit_dt = today_dt
                date_label = "今天 (日间自适应)"
        visit_date = visit_dt.strftime("%Y-%m-%d")
    else:
        raw_date_arg = args.visit_date.strip().lower()
        if raw_date_arg in ("today", "今", "今日", "今天"):
            visit_dt = today_dt
            date_label = "今天"
            visit_date = visit_dt.strftime("%Y-%m-%d")
        elif raw_date_arg in ("tomorrow", "明", "明日", "明天"):
            visit_dt = today_dt + timedelta(days=1)
            date_label = "明天"
            visit_date = visit_dt.strftime("%Y-%m-%d")
        else:
            visit_date = args.visit_date.strip()
            date_label = "指定日期"

    # Resolve departure time
    dep_time = args.departure_time or cfg.get("default_departure_time", "09:30")

    # Resolve origin
    origin = cfg.get("default_origin", {})
    origin_name = args.origin_name or origin.get("name", "Field Operations Base")
    origin_address = args.origin_address or origin.get("address", "North York, Toronto, ON")
    origin_lat = args.origin_lat or origin.get("latitude", 43.7615)
    origin_lng = args.origin_lng or origin.get("longitude", -79.4111)

    if args.origin:
        resolved_origin = resolve_origin_input(args.origin, api_key=cfg.get("google_maps_api_key"))
        origin_name = resolved_origin["name"]
        origin_address = resolved_origin["address"]
        origin_lat = resolved_origin["latitude"]
        origin_lng = resolved_origin["longitude"]

    # Target Count
    target_count = args.count or cfg.get("target_count", 30)

    # Directions and corridor settings
    corridor_cfg = cfg.get("corridor_settings", {})
    direction_str = args.direction or corridor_cfg.get("default_direction", "east")
    bearing = parse_bearing(direction_str, cfg.get("directions", {}))

    corridor_width_km = args.corridor_width or corridor_cfg.get("corridor_width_km", 4.5)
    max_depth_km = args.max_depth or (args.radius if not args.search_center else None) or corridor_cfg.get("max_search_depth_km", 28.0)
    step_km = corridor_cfg.get("step_distance_km", 3.0)

    # Place types, template, and criteria resolution
    place_types_arg = args.place_types
    if place_types_arg:
        place_types = [t.strip() for t in place_types_arg.split(",") if t.strip()]
    else:
        place_types = cfg.get("place_types", ["point_of_interest"])

    template = (args.template or cfg.get("template", "general")).strip().lower()
    criteria = (args.criteria or cfg.get("criteria", "")).strip()

    print("\n" + "=" * 70)
    print("Google Maps Place Scout & Corridor Planner")
    print("=" * 70)
    print(f"规划拜访日期: {visit_date} ({date_label}) | 预定出发: {dep_time}")
    print(f"出发起点: {origin_name} ({origin_address})")
    print(f"扫街推进方向: {direction_str.upper()} (航向角 {bearing}°)")
    print(f"走廊覆盖参数: 宽度 ±{corridor_width_km} km | 纵深 {max_depth_km} km")
    print(f"目标类型与标准: 类型 [{', '.join(place_types)}] | 模板 [{template}]" + (f" | 判定标准: {criteria}" if criteria else ""))
    print(f"目标锁定数量: {target_count} 家英文 Google Maps 场所\n")

    # Paths resolution
    paths_cfg = cfg.get("paths", {})
    contracted_source = args.contracted_source or paths_cfg.get("contracted_file", "")
    visited_source = args.visited_source or paths_cfg.get("visited_file", "")

    def resolve_source(s: str) -> str:
        if not s:
            return ""
        if s.startswith("http://") or s.startswith("https://") or os.path.isabs(s):
            return s
        return os.path.join(SKILL_DIR, s)

    contracted_path = resolve_source(contracted_source)
    visited_path = resolve_source(visited_source)

    # Output directory: project output folder or user cache routes
    paths_env = get_effective_storage_paths()
    configured_output = paths_cfg.get("output_dir", "output")
    if os.path.isabs(configured_output):
        output_dir = configured_output
    elif paths_env["is_project_mode"]:
        output_dir = os.path.join(paths_env["project_root"], configured_output)
    else:
        output_dir = get_user_cache_dir("routes")
    os.makedirs(output_dir, exist_ok=True)

    # Intermediate audit directory: system temporary directory
    temp_audit_dir = get_temp_dir("audit")

    # Initialize SheetExporter
    exporter = SheetExporter(output_dir=output_dir, template=template)
    gs_cfg = cfg.get("google_sheets", {})
    webhook_url = gs_cfg.get("webhook_url") or gs_cfg.get("spreadsheet_url") or gs_cfg.get("spreadsheet_id", "")

    execution_context = {
        "visit_date": visit_date,
        "direction": direction_str.upper(),
        "origin_str": f"{origin_name} ({origin_address})"
    }

    is_radial = direction_str.lower() in ("radial", "around", "nearby", "circle", "radius")

    try:
        api_key = cfg.get("google_maps_api_key", "").strip()
        referer = cfg.get("google_maps_api_referer", "").strip()

        # Decouple departure origin from search target center
        search_name = origin_name
        search_lat = origin_lat
        search_lng = origin_lng
        search_radius_km = args.search_radius or (args.radius if args.search_center else max_depth_km) or 2.0

        if args.search_center:
            resolved_search = resolve_origin_input(args.search_center, api_key=api_key)
            search_name = resolved_search["name"]
            search_lat = resolved_search["latitude"]
            search_lng = resolved_search["longitude"]
            print(f"  [Config] 目标搜索中心已解耦: {search_name} ({search_lat:.4f}, {search_lng:.4f})")

        # Step 1: Generate corridor or radial probes and search Google Places in English
        if args.search_center or is_radial:
            radius_to_probe = search_radius_km if args.search_center else max_depth_km
            print(f"【步骤 1/5】以目标商圈 [{search_name}] 为核心探测并检索周边 {radius_to_probe} km 英文 Google Maps 场所...")
            probes = generate_radial_probe_points(search_lat, search_lng, radius_km=radius_to_probe)
            print(f"  以目标商圈为中心部署了 {len(probes)} 个径向探测点 (半径 {radius_to_probe} km)")
        else:
            print("【步骤 1/5】沿推进方向探测并检索英文 Google Maps 场所...")
            probes = generate_corridor_probe_points(origin_lat, origin_lng, bearing, step_km=step_km, max_depth_km=max_depth_km)
            print(f"  沿航向角 {bearing}° 部署了 {len(probes)} 个探测中心点 (间距 {step_km} km)")

        searcher = PlacesSearcher(api_key=api_key, referer=referer)
        if args.keywords:
            keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
        else:
            configured_kws = cfg.get("keywords")
            if configured_kws:
                keywords = configured_kws
            else:
                keywords = [t.replace("_", " ") for t in place_types]

        probe_radius_m = int(search_radius_km * 1000 if args.search_center else corridor_width_km * 1000)
        raw_candidates = searcher.search_corridor_probes(
            probes,
            keywords=keywords,
            radius_meters=probe_radius_m,
            target_count=target_count,
            place_types=place_types
        )

        # Apply bounding box hard filter if requested
        if args.bounds:
            try:
                b_parts = [float(x.strip()) for x in args.bounds.split(",")]
                if len(b_parts) == 4:
                    min_lat, min_lng, max_lat, max_lng = b_parts
                    before_cnt = len(raw_candidates)
                    raw_candidates = [
                        c for c in raw_candidates
                        if min_lat <= float(c.get("latitude", 0)) <= max_lat and min_lng <= float(c.get("longitude", 0)) <= max_lng
                    ]
                    print(f"  [Geofence] 严格应用矩形地理边框过滤 ({before_cnt} -> {len(raw_candidates)} 家保留)")
            except Exception as e:
                print(f"  [Warning] 矩形边框参数解析失败: {e}")

        # Check if user specified any mandatory places via URL or address that need to be injected into candidates
        if args.include_places:
            mand_list = [m.strip() for m in args.include_places.split(",") if m.strip()]
            for item in mand_list:
                if "http" in item or re.search(r"\d", item):
                    resolved_mand = resolve_origin_input(item, api_key=api_key)
                    exists = any(resolved_mand["name"].lower() in (r.get("name") or "").lower() for r in raw_candidates)
                    if not exists and resolved_mand.get("latitude") and resolved_mand.get("longitude"):
                        injected = {
                            "name": resolved_mand["name"],
                            "address": resolved_mand["address"],
                            "latitude": resolved_mand["latitude"],
                            "longitude": resolved_mand["longitude"],
                            "placeId": f"custom_pinned_{len(raw_candidates)+1}",
                            "phone": "None",
                            "openingHours": "11:00 AM – 10:00 PM",
                            "_is_pinned": True
                        }
                        raw_candidates.insert(0, injected)

        print(f"  初始检索到 {len(raw_candidates)} 家潜在目标场所。\n")

        # Step 2: Apply Filters (Contracted CRM & Visited Sheets + Spatial Geofencing + Manual Overrides)
        print("【步骤 2/5】执行场所过滤规则 (多源自适应摄取 + 模型语义实体对齐 + 地理空间与场所自定义约束)...")
        exclude_regions = [r.strip() for r in args.exclude_regions.split(",")] if args.exclude_regions else []
        include_regions = [r.strip() for r in args.include_regions.split(",")] if args.include_regions else []
        exclude_places = [r.strip() for r in args.exclude_places.split(",")] if args.exclude_places else []
        mandatory_places = [r.strip() for r in args.include_places.split(",")] if args.include_places else []

        r_filter = PlaceFilter(
            contracted_path=contracted_path,
            visited_path=visited_path,
            exclude_regions=exclude_regions,
            include_regions=include_regions,
            exclude_places=exclude_places,
            mandatory_places=mandatory_places,
            visit_date=visit_date,
            departure_time=dep_time,
            filter_closed=not args.keep_closed,
            allow_dinner_only=args.allow_dinner_only or cfg.get("allow_dinner_only", False),
            openrouter_api_key=openrouter_api_key,
            place_types=place_types,
            keywords=keywords,
            locality=origin_address
        )
        filtered_candidates, filter_stats = r_filter.filter_places(raw_candidates)
        if filter_stats.get("mandatory_count", 0) > 0:
            print(f"  包含用户指定必选场所: {filter_stats['mandatory_count']} 家")
            for item in filter_stats.get("mandatory_details", []):
                print(f"     ↳ {item['name']}: {item['reason']}")
        if filter_stats.get("excluded_manual", 0) > 0:
            print(f"  排除用户指定排除场所: {filter_stats['excluded_manual']} 家")
            for item in filter_stats.get("manual_details", [])[:3]:
                print(f"     ↳ {item['name']}: {item['reason']}")
        if filter_stats.get("excluded_regions", 0) > 0:
            print(f"  排除避开/禁行区域场所: {filter_stats['excluded_regions']} 家")
            for item in filter_stats.get("region_details", [])[:3]:
                print(f"     ↳ {item['name']}: {item['reason']}")
        if filter_stats.get("excluded_closed", 0) > 0:
            print(f"  排除未开业/公休/纯夜间场所: {filter_stats['excluded_closed']} 家")
            for item in filter_stats.get("closed_details", [])[:3]:
                print(f"     ↳ {item['name']}: {item['reason']}")
        print(f"  排除已签约商家 (CRM): {filter_stats['excluded_contracted']} 家")
        print(f"  排除已拜访商家 (Sheet/API): {filter_stats['excluded_visited']} 家")
        print(f"     - 硬匹配精确命中: {filter_stats['hard_match_count']} 家 (ID / 规范电话)")
        print(f"     - 模型语义对齐命中: {filter_stats['semantic_match_count']} 家 (品牌别名 / 商圈对齐)")

        semantic_samples = [d for d in filter_stats.get("contracted_details", []) + filter_stats.get("visited_details", []) if d.get("tier") == "semantic_model"]
        for s in semantic_samples[:3]:
            print(f"       ↳ {s['name']}: {s['reason']}")

        print(f"  满足准入条件的开拓候选场所: {filter_stats['accepted_count']} 家\n")

        # Step 3: Agent-Native Cascaded Active Multimodal Audit (Jev Fast Text Triage + Agent Vision)
        print("【步骤 3/5】执行基于置信度的级联主动研判 (Jev 文本快筛 + 智能体原生多模态视觉精审)...")
        audit_mgr = PlaceAuditManager(
            audit_dir=temp_audit_dir,
            openrouter_api_key=openrouter_api_key,
            criteria=criteria,
            template=template,
            keywords=keywords
        )
        pending_audit_file = audit_mgr.export_pending_audit(filtered_candidates)
        ambiguous_items = audit_mgr.ambiguous_candidates

        if ambiguous_items:
            print(f"  [智能体多模态提示] 发现 {len(ambiguous_items)} 家存疑场所 (Jev 判定模糊)，已下载照片至系统临时目录:")
            for amb in ambiguous_items:
                photos_str = ", ".join([os.path.basename(p) for p in amb.get("local_photo_paths", [])]) or "无可用照片"
                print(f"     ↳ {amb.get('name')} ({amb.get('primaryType')}): Jev 概率 {int(amb.get('jev_confidence', 0.5)*100)}% | 本地照片: {photos_str}")
            print(f"     待审清单已就绪: {pending_audit_file}")

        if args.dump_audit_only:
            results_file = os.path.join(temp_audit_dir, "agent_audit_results.json")
            print(f"  [--dump-audit-only 触发] 已导出待审核清单至临时目录: {pending_audit_file}")
            print(f"     请智能体调用 view_file 查验上述照片后，将审核结果写入: {results_file} 并重新执行。")
            return {"status": "paused_for_agent_audit", "pending_file": pending_audit_file, "ambiguous_count": len(ambiguous_items)}

        verified_candidates = []
        for c in filtered_candidates:
            if c.get("_is_pinned"):
                c_copy = dict(c)
                pinned_feat = c.get("_matched_features") or ["Special request (User Pinned)"]
                c_copy["_matched_features"] = pinned_feat
                c_copy["_audit_rationale"] = "Priority visit (User pinned place)"
                verified_candidates.append(c_copy)
                continue
            is_match, confidence, features, rationale = audit_mgr.audit_place(c)
            if is_match:
                c_copy = dict(c)
                c_copy["_matched_features"] = features
                c_copy["_audit_rationale"] = rationale
                verified_candidates.append(c_copy)

        st = audit_mgr.stats
        print(f"  审核完成，分级决策统计:")
        print(f"     - Jev 文本高置信直通 (Pass): {st.get('jev_pass', 0)} 家")
        print(f"     - Jev 文本明确排除 (Reject): {st.get('jev_reject', 0)} 家")
        if st.get("agent_cached", 0) > 0:
            print(f"     - 智能体多模态视觉审核确认 (Agent Verified): {st.get('agent_cached', 0)} 家")
        if st.get("ambiguous_need_agent", 0) > 0:
            print(f"     - 存疑场所 (待智能体多模态精审): {st.get('ambiguous_need_agent', 0)} 家")
        if st.get("heuristic", 0) > 0:
            print(f"     - 本地规则后备兜底 (Heuristic): {st.get('heuristic', 0)} 家")
        print(f"  最终确认符合研判标准的有效场所: {len(verified_candidates)} 家\n")

        # Step 4: Directional Corridor Slice or Proximity Radial Route Planning
        router_bearing = bearing
        router_max_depth = max_depth_km
        router_corridor_width = corridor_width_km
        effective_radial = is_radial

        if args.search_center and (search_lat != origin_lat or search_lng != origin_lng):
            d_origin_target = haversine_distance_km(origin_lat, origin_lng, search_lat, search_lng)
            if not args.direction:
                mean_lat = math.radians((origin_lat + search_lat) / 2)
                dx = (search_lng - origin_lng) * (math.pi / 180.0) * 6371.0 * math.cos(mean_lat)
                dy = (search_lat - origin_lat) * (math.pi / 180.0) * 6371.0
                router_bearing = (math.degrees(math.atan2(dx, dy)) + 360) % 360
            router_max_depth = max(max_depth_km, d_origin_target + search_radius_km + 15.0)
            router_corridor_width = max(corridor_width_km, search_radius_km + 12.0)
            effective_radial = False

        router = DirectionalRouter(
            origin=origin,
            bearing_degrees=router_bearing,
            corridor_width_km=router_corridor_width,
            max_depth_km=router_max_depth,
            slice_length_km=2.0
        )
        if effective_radial:
            print(f"【步骤 4/5】执行锚点周边径向紧凑聚类路线规划 (Proximity Radial Tour, 半径 {max_depth_km} km)...")
            final_stops = router.plan_radial_route(verified_candidates, target_count=target_count)
        else:
            print(f"【步骤 4/5】执行防折返跑走廊分片局部聚类推进算法 (Corridor Slice Sweep, 航向 {router_bearing:.1f}°)...")
            final_stops = router.plan_unidirectional_route(verified_candidates, target_count=target_count)

        if len(final_stops) < target_count:
            print(f"[Notice] 提示: 走廊内符合单向条件的场所共 {len(final_stops)} 家 (少于预设目标 {target_count} 家)。")
        else:
            print(f"  成功沿单向走廊优选锁定 {len(final_stops)} 家确定拜访场所！")

        # Step 5: Generate Slash Navigation URL and Export 7-Column Spreadsheet
        print(f"\n【步骤 5/5】生成斜杠拼接 {len(final_stops)} 站导航 URL 并导出规范数据表...")
        route_gen = RouteGenerator(
            origin_name=origin_name,
            origin_address=origin_address,
            origin_lat=origin_lat,
            origin_lng=origin_lng,
            departure_time_str=dep_time
        )
        processed_stops, master_slash_url, legs = route_gen.process_route(final_stops, visit_date_str=visit_date)

        file_prefix = f"route_{visit_date}_{direction_str}"
        excel_path = exporter.export_excel(processed_stops, f"{file_prefix}.xlsx", master_nav_url=master_slash_url)

        # Google Sheets sync check (Google Apps Script Webhook)
        if gs_cfg.get("enabled") and webhook_url:
            configured_tab = gs_cfg.get("sheet_name")
            if not configured_tab or configured_tab in ("Daily Field Sales Route", "Daily Places Scout Route"):
                sheet_tab_name = f"{visit_date} ({direction_str.upper()})"
            else:
                sheet_tab_name = configured_tab
            exporter.sync_to_google_sheet(
                spreadsheet_id=webhook_url,
                sheet_name=sheet_tab_name,
                stops=processed_stops,
                master_nav_url=master_slash_url
            )

        if excel_path:
            print(f"  7 列纯英文 Excel (.xlsx) 清单已生成 (含导航地址合并去重): {excel_path}")

        meta_report = {
            "visit_date": visit_date,
            "date_label": date_label,
            "direction": direction_str.upper(),
            "origin_str": f"{origin_name} ({origin_address})",
            "master_nav_url": master_slash_url,
            "sheet_url": webhook_url or "",
            "legs": legs,
            "excel_path": excel_path
        }
        summary_md_path = exporter.export_markdown_summary(processed_stops, f"{file_prefix}_summary.md", meta_report)
        latest_md_path = exporter.export_markdown_summary(processed_stops, "latest_route_summary.md", meta_report)
        latest_json_path = os.path.join(output_dir, "latest_route_summary.json")
        try:
            with open(latest_json_path, "w", encoding="utf-8") as f:
                json.dump({
                    "visit_date": visit_date,
                    "date_label": date_label,
                    "direction": direction_str.upper(),
                    "origin": origin,
                    "stops_count": len(processed_stops),
                    "master_slash_url": master_slash_url,
                    "sheet_url": meta_report["sheet_url"],
                    "excel_path": excel_path,
                    "summary_md_path": summary_md_path,
                    "stops": processed_stops
                }, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        print(f"  Markdown 格式总结报告已生成: {summary_md_path}")

        print("\n" + "=" * 70)
        print("【AGENT IN-CHAT REPORT / 请智能体直接在对话窗口展示以下内容】")
        print("=" * 70)
        try:
            with open(summary_md_path, "r", encoding="utf-8") as f:
                print(f.read())
        except Exception:
            pass
        print("=" * 70)
        print("每日工作流执行完毕！\n")

        return {
            "status": "success",
            "visit_date": visit_date,
            "stops_count": len(processed_stops),
            "master_slash_url": master_slash_url,
            "excel_path": excel_path,
            "summary_md_path": summary_md_path,
            "latest_json_path": latest_json_path
        }

    except Exception as exc:
        log_file = exporter.report_failure_to_google_sheet(
            spreadsheet_id=webhook_url,
            error=exc,
            context=execution_context
        )
        return {
            "status": "failed",
            "error": str(exc),
            "log_path": log_file
        }

if __name__ == "__main__":
    result = main()
    if isinstance(result, dict) and result.get("status") == "failed":
        sys.exit(1)
